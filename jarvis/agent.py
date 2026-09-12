"""The Jarvis agent loop: the LLM picks tools, Jarvis runs them behind the gate."""

from __future__ import annotations

import json
import logging
from typing import Any

import anyio
from pydantic import BaseModel, Field

from jarvis.llm.base import LLMError, LLMProvider, Message, ToolCall
from jarvis.security import Decision, PolicyEngine, summarize_call
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import REGISTRY, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Jarvis, a personal assistant that helps the user understand and control \
their Windows PC.

Rules:
- Use the provided tools to answer anything about the machine's real state. Never \
  guess CPU, memory, disk, battery, process, file, git, Docker or Kubernetes values.
- Call tools in parallel when the question needs several facts.
- Answer in a few short sentences. Report concrete numbers with units; round \
  sensibly. Use a compact markdown list or table when several values are involved.
- Some tools need the user's confirmation. If a tool reports that, tell the user \
  exactly what you are about to do and stop -- do not retry it.
- If a tool fails, say plainly what failed and suggest the next step.
- If a request is outside your tools, say so instead of inventing a result.
- Be direct and conversational. No preamble, no restating the question.
"""

#: Hard ceiling on LLM<->tool round trips for one user message.
MAX_ITERATIONS = 6

#: Tool payloads larger than this are truncated before going back to the model.
MAX_TOOL_RESULT_CHARS = 6000


class ToolEvent(BaseModel):
    """One executed (or refused) tool call, surfaced to the UI and audit log."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    permission: PermissionLevel
    decision: Decision = Decision.ALLOW
    reason: str = ""
    duration_ms: int = 0
    error: str | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.decision is Decision.CONFIRM


class AgentResult(BaseModel):
    """Outcome of one user turn."""

    reply: str
    tool_events: list[ToolEvent] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    #: Calls the user must approve before Jarvis will run them.
    pending_confirmations: list[ToolCall] = Field(default_factory=list)


class JarvisAgent:
    """Drives the model/tool loop for a single conversation turn."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry | None = None,
        policy: PolicyEngine | None = None,
        *,
        max_iterations: int = MAX_ITERATIONS,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._provider = provider
        self._registry = registry or REGISTRY
        self._policy = policy or PolicyEngine.from_settings()
        self._max_iterations = max_iterations
        self._system_prompt = system_prompt

    async def run(
        self,
        user_message: str,
        history: list[Message] | None = None,
        *,
        approved_tools: set[str] | None = None,
    ) -> AgentResult:
        """Answer one user message, executing tools as the model requests them."""
        turn = Message.user(user_message)
        messages = [*(history or []), turn]
        return await self._loop(messages, [turn], approved_tools or set())

    async def resume_with_approval(
        self, call: ToolCall, history: list[Message] | None = None
    ) -> AgentResult:
        """Run a call the user has just approved, then let the model report back.

        The approved call is executed directly rather than handed back to the
        model, so what runs is exactly what the user saw and approved.
        """
        result, event = await anyio.to_thread.run_sync(
            self._execute_one, call, {call.name}
        )
        note = Message.user(
            f"[jarvis] The user approved '{call.name}'. Jarvis executed it. "
            f"Result: {_serialize(result)}. Report the outcome to the user.",
            hidden=True,
        )
        messages = [*(history or []), note]
        outcome = await self._loop(messages, [note], {call.name})
        outcome.tool_events.insert(0, event)
        return outcome

    async def _loop(
        self,
        messages: list[Message],
        new_messages: list[Message],
        approved: set[str],
    ) -> AgentResult:
        events: list[ToolEvent] = []
        pending: list[ToolCall] = []

        for _ in range(self._max_iterations):
            try:
                response = await self._provider.complete(
                    messages,
                    system_prompt=self._system_prompt,
                    tools=self._registry.declarations(),
                )
            except LLMError as exc:
                reply = f"I couldn't reach the language model: {exc}"
                assistant = Message.assistant(reply)
                new_messages.append(assistant)
                return AgentResult(
                    reply=reply, tool_events=events, messages=new_messages
                )

            assistant = Message.assistant(response.text, response.tool_calls)
            messages.append(assistant)
            new_messages.append(assistant)

            if not response.wants_tools:
                return AgentResult(
                    reply=response.text or "(no response)",
                    tool_events=events,
                    messages=new_messages,
                    pending_confirmations=pending,
                )

            results = await self._execute_calls(response.tool_calls, approved)
            for call, (result, event) in zip(response.tool_calls, results):
                events.append(event)
                if event.needs_confirmation:
                    pending.append(call)
                tool_message = Message.tool(call.name, _serialize(result))
                messages.append(tool_message)
                new_messages.append(tool_message)

        reply = "I hit my tool-call limit for this request. Try narrowing the question."
        fallback = Message.assistant(reply)
        new_messages.append(fallback)
        return AgentResult(
            reply=reply,
            tool_events=events,
            messages=new_messages,
            pending_confirmations=pending,
        )

    async def _execute_calls(
        self, calls: list[ToolCall], approved: set[str]
    ) -> list[tuple[ToolResult, ToolEvent]]:
        """Run every requested tool concurrently, in worker threads."""
        results: list[tuple[ToolResult, ToolEvent] | None] = [None] * len(calls)

        async def run_one(index: int, call: ToolCall) -> None:
            results[index] = await anyio.to_thread.run_sync(
                self._execute_one, call, approved
            )

        async with anyio.create_task_group() as task_group:
            for index, call in enumerate(calls):
                task_group.start_soon(run_one, index, call)
        return [result for result in results if result is not None]

    def _execute_one(
        self, call: ToolCall, approved: set[str]
    ) -> tuple[ToolResult, ToolEvent]:
        """Policy gate, then execution. Every path returns a result and an event."""
        spec = self._registry.get(call.name)
        verdict = self._policy.evaluate(
            spec, tool_name=call.name, approved=call.name in approved
        )

        if verdict.decision is not Decision.ALLOW:
            message = (
                f"confirmation required: {summarize_call(call.name, call.args)}"
                if verdict.decision is Decision.CONFIRM
                else verdict.reason
            )
            logger.info(
                "policy %s for %s: %s", verdict.decision.value, call.name, verdict.reason
            )
            return (
                ToolResult(
                    ok=False,
                    tool=call.name,
                    error=message,
                    needs_confirmation=verdict.decision is Decision.CONFIRM,
                ),
                ToolEvent(
                    name=call.name,
                    args=call.args,
                    ok=False,
                    permission=verdict.permission,
                    decision=verdict.decision,
                    reason=verdict.reason,
                    error=message,
                ),
            )

        result = self._registry.execute(call.name, call.args)
        logger.info(
            "tool %s ok=%s in %dms", call.name, result.ok, result.duration_ms
        )
        return result, ToolEvent(
            name=call.name,
            args=call.args,
            ok=result.ok,
            permission=verdict.permission,
            decision=Decision.ALLOW,
            reason=verdict.reason,
            duration_ms=result.duration_ms,
            error=result.error,
        )


def _serialize(result: ToolResult) -> str:
    payload: dict[str, Any] = {"ok": result.ok}
    if result.ok:
        payload["data"] = result.data
    else:
        payload["error"] = result.error
    text = json.dumps(payload, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + '... (truncated)"}'
    return text
