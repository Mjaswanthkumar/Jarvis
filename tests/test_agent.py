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
