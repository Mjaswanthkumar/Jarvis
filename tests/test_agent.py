from __future__ import annotations

import json

import pytest

from jarvis.agent import JarvisAgent
from jarvis.llm.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    Role,
    ToolCall,
)
from jarvis.security import Decision, PolicyEngine
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import ToolRegistry, tool


class ScriptedProvider(LLMProvider):
    """Replays a fixed list of responses and records what it was sent."""

    name = "scripted"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.seen: list[list[Message]] = []

    def is_configured(self) -> bool:
        return True

    async def complete(self, messages, *, system_prompt, tools=None) -> LLMResponse:
        self.seen.append(list(messages))
        if not self._responses:
            return LLMResponse(text="done")
        return self._responses.pop(0)


class FailingProvider(LLMProvider):
    name = "failing"

    def is_configured(self) -> bool:
        return True

    async def complete(self, messages, *, system_prompt, tools=None) -> LLMResponse:
        raise LLMError("quota exceeded")


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        description="echo a number",
        permission=PermissionLevel.READ_ONLY,
        registry=reg,
    )
    def echo(value: int) -> dict[str, int]:
        return {"value": value}

    @tool(
        description="pretend to do something risky",
        permission=PermissionLevel.CONFIRM_REQUIRED,
        registry=reg,
    )
    def risky() -> str:
        return "executed"

    return reg


@pytest.mark.asyncio
async def test_agent_answers_without_tools(registry: ToolRegistry) -> None:
    provider = ScriptedProvider([LLMResponse(text="All good.")])
    result = await JarvisAgent(provider, registry).run("hi")
    assert result.reply == "All good."
    assert result.tool_events == []


@pytest.mark.asyncio
async def test_agent_runs_tool_and_feeds_result_back(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="echo", args={"value": 7})]),
            LLMResponse(text="The value is 7."),
        ]
    )
    result = await JarvisAgent(provider, registry).run("echo 7")

    assert result.reply == "The value is 7."
    assert [e.name for e in result.tool_events] == ["echo"]
    assert result.tool_events[0].ok

    tool_messages = [m for m in provider.seen[-1] if m.role is Role.TOOL]
    assert json.loads(tool_messages[0].content) == {"ok": True, "data": {"value": 7}}


@pytest.mark.asyncio
async def test_parallel_tool_calls_are_all_executed(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(name="echo", args={"value": 1}),
                    ToolCall(name="echo", args={"value": 2}),
                ]
            ),
            LLMResponse(text="1 and 2"),
        ]
    )
    result = await JarvisAgent(provider, registry).run("echo both")
    assert len(result.tool_events) == 2
    assert all(event.ok for event in result.tool_events)


@pytest.mark.asyncio
async def test_confirm_required_tool_is_not_run_without_approval(
    registry: ToolRegistry,
) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="risky")]),
            LLMResponse(text="I need your approval first."),
        ]
    )
    result = await JarvisAgent(provider, registry).run("do the risky thing")

    event = result.tool_events[0]
    assert event.needs_confirmation and not event.ok
    assert [c.name for c in result.pending_confirmations] == ["risky"]

    tool_message = [m for m in provider.seen[-1] if m.role is Role.TOOL][0]
    assert "confirmation required" in tool_message.content


@pytest.mark.asyncio
async def test_confirm_required_tool_runs_once_approved(
    registry: ToolRegistry,
) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="risky")]),
            LLMResponse(text="Done."),
        ]
    )
    result = await JarvisAgent(provider, registry).run(
        "do it", approved_tools={"risky"}
    )
    assert result.tool_events[0].ok
    assert result.pending_confirmations == []


@pytest.mark.asyncio
async def test_iteration_limit_stops_tool_loops(registry: ToolRegistry) -> None:
    looping = [
        LLMResponse(tool_calls=[ToolCall(name="echo", args={"value": 1})])
        for _ in range(10)
    ]
    provider = ScriptedProvider(looping)
    result = await JarvisAgent(provider, registry, max_iterations=3).run("loop")
    assert "tool-call limit" in result.reply
    assert len(result.tool_events) == 3


@pytest.mark.asyncio
async def test_llm_failure_is_reported_not_raised(registry: ToolRegistry) -> None:
    result = await JarvisAgent(FailingProvider(), registry).run("hi")
    assert "quota exceeded" in result.reply


@pytest.mark.asyncio
async def test_history_is_passed_to_the_provider(registry: ToolRegistry) -> None:
    provider = ScriptedProvider([LLMResponse(text="ok")])
    history = [Message.user("earlier"), Message.assistant("noted")]
    await JarvisAgent(provider, registry).run("now", history)
    assert [m.content for m in provider.seen[0]] == ["earlier", "noted", "now"]


@pytest.mark.asyncio
async def test_resume_with_approval_executes_the_exact_call(
    registry: ToolRegistry,
) -> None:
    """The approved call runs as-is; the model only narrates the outcome."""
    provider = ScriptedProvider([LLMResponse(text="Done, it is closed.")])
    agent = JarvisAgent(provider, registry)

    result = await agent.resume_with_approval(ToolCall(name="risky"))

    assert result.reply == "Done, it is closed."
    assert result.tool_events[0].name == "risky"
    assert result.tool_events[0].ok is True

    note = provider.seen[0][-1]
    assert note.hidden is True
    assert "approved" in note.content and "executed" in note.content


@pytest.mark.asyncio
async def test_policy_blocklist_stops_a_tool_the_model_asks_for(
    registry: ToolRegistry,
) -> None:
    from jarvis.security import Decision, PolicyEngine

    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="echo", args={"value": 1})]),
            LLMResponse(text="I cannot use that tool."),
        ]
    )
    agent = JarvisAgent(provider, registry, PolicyEngine(blocked_tools={"echo"}))
    result = await agent.run("echo")

    event = result.tool_events[0]
    assert event.decision is Decision.DENY
    assert not event.ok
    assert result.pending_confirmations == []


@pytest.mark.asyncio
async def test_read_only_mode_refuses_state_changing_tools(
    registry: ToolRegistry,
) -> None:
    from jarvis.security import Decision, PolicyEngine

    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="risky")]),
            LLMResponse(text="Read-only mode is on."),
        ]
    )
    agent = JarvisAgent(provider, registry, PolicyEngine(read_only_mode=True))
    result = await agent.run("close it", approved_tools={"risky"})

    assert result.tool_events[0].decision is Decision.DENY
    assert "read-only mode" in (result.tool_events[0].reason or "")


# ------------------------------------------------ taint through the loop ----
@pytest.fixture()
def taint_registry() -> ToolRegistry:
    """A registry with a file-like reader and a launcher, as in production."""
    reg = ToolRegistry()

    @tool(
        description="reads third-party text from disk",
        permission=PermissionLevel.READ_ONLY,
        untrusted_output=True,
        registry=reg,
    )
    def read_file() -> dict[str, str]:
        return {"content": "ordinary notes, nothing suspicious"}

    @tool(
        description="reads a number Jarvis computed itself",
        permission=PermissionLevel.READ_ONLY,
        registry=reg,
    )
    def read_metric() -> dict[str, int]:
        return {"percent": 42}

    @tool(
        description="launches something, normally without asking",
        permission=PermissionLevel.LOW_RISK,
        registry=reg,
    )
    def launch(name: str = "x") -> str:
        return f"launched {name}"

    return reg


@pytest.mark.asyncio
async def test_low_risk_runs_freely_in_a_clean_turn(
    taint_registry: ToolRegistry,
) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="read_metric")]),
            LLMResponse(tool_calls=[ToolCall(name="launch")]),
            LLMResponse(text="done"),
        ]
    )
    result = await JarvisAgent(provider, taint_registry).run("check then launch")

    launched = [e for e in result.tool_events if e.name == "launch"][0]
    assert launched.ok is True
    assert launched.tainted is False


@pytest.mark.asyncio
async def test_reading_a_file_gates_a_later_low_risk_action(
    taint_registry: ToolRegistry,
) -> None:
    """The closed gap: content on disk must not silently trigger an action."""
    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="read_file")]),
            LLMResponse(tool_calls=[ToolCall(name="launch", args={"name": "calc"})]),
            LLMResponse(text="I need your approval."),
        ]
    )
    result = await JarvisAgent(provider, taint_registry).run("read it then open it")

    launched = [e for e in result.tool_events if e.name == "launch"][0]
    assert launched.ok is False
    assert launched.decision is Decision.CONFIRM
    assert launched.tainted is True
    assert result.escalated_by_taint is True
    assert [c.name for c in result.pending_confirmations] == ["launch"]


@pytest.mark.asyncio
async def test_taint_does_not_apply_to_siblings_in_the_same_batch(
    taint_registry: ToolRegistry,
) -> None:
    """Both calls were chosen before either result existed, so neither taints
    the other; taint takes effect from the next iteration."""
    provider = ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(name="read_file"), ToolCall(name="launch")]
            ),
            LLMResponse(text="done"),
        ]
    )
    result = await JarvisAgent(provider, taint_registry).run("both at once")

    launched = [e for e in result.tool_events if e.name == "launch"][0]
    assert launched.ok is True


@pytest.mark.asyncio
async def test_taint_mode_off_leaves_the_action_ungated(
    taint_registry: ToolRegistry,
) -> None:
    from jarvis.security import TaintMode

    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="read_file")]),
            LLMResponse(tool_calls=[ToolCall(name="launch")]),
            LLMResponse(text="done"),
        ]
    )
    agent = JarvisAgent(
        provider, taint_registry, PolicyEngine(taint_mode=TaintMode.OFF)
    )
    result = await agent.run("read then launch")
    assert [e for e in result.tool_events if e.name == "launch"][0].ok is True


@pytest.mark.asyncio
async def test_suspicious_mode_only_taints_on_a_detector_hit(
    taint_registry: ToolRegistry,
) -> None:
    from jarvis.security import TaintMode

    provider = ScriptedProvider(
        [
            LLMResponse(tool_calls=[ToolCall(name="read_file")]),
            LLMResponse(tool_calls=[ToolCall(name="launch")]),
            LLMResponse(text="done"),
        ]
    )
    agent = JarvisAgent(
        provider, taint_registry, PolicyEngine(taint_mode=TaintMode.SUSPICIOUS)
    )
    result = await agent.run("read benign file then launch")
    # The file is benign, so nothing trips the detector and nothing escalates.
    assert [e for e in result.tool_events if e.name == "launch"][0].ok is True
