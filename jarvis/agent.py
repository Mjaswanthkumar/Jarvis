"""The Jarvis agent loop: LLM picks tools, Jarvis runs them behind the gate."""

from __future__ import annotations

import json
import logging
from typing import Any

import anyio
from pydantic import BaseModel, Field

from jarvis.llm.base import LLMError, LLMProvider, Message, Role, ToolCall
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import REGISTRY, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Jarvis, a personal assistant that helps the user understand and control \
their Windows PC.

Rules:
- Use the provided tools to answer anything about the machine's real state. Never \
  guess CPU, memory, disk, battery, process or network values.
- Call tools in parallel when the question needs several facts.
- Answer in a few short sentences. Report concrete numbers with units; round \
  sensibly. Use a compact markdown list or table when several values are involved.
- If a tool fails, say plainly what failed and suggest the next step.
- If a request is outside your tools, say so instead of inventing a result.
- Be direct and conversational. No preamble, no restating the question.
"""

#: Hard ceiling on LLM<->tool round trips for one user message.
MAX_ITERATIONS = 6

#: Tool payloads larger than this are truncated before going back to the model.
MAX_TOOL_RESULT_CHARS = 6000


class ToolEvent(BaseModel):
    """One executed (or refused) tool call, surfaced to the UI."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    permission: PermissionLevel
    duration_ms: int = 0
    error: str | None = None
    needs_confirmation: bool = False


class AgentResult(BaseModel):
    """Outcome of one user turn."""

    reply: str
    tool_events: list[ToolEvent] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    pending_confirmations: list[ToolCall] = Field(default_factory=list)


class JarvisAgent:
    """Drives the model/tool loop for a single conversation turn."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry | None = None,
        *,
        max_iterations: int = MAX_ITERATIONS,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._provider = provider
        self._registry = registry or REGISTRY
        self._max_iterations = max_iterations
        self._system_prompt = system_prompt

    async def run(
        self,
        user_message: str,
        history: list[Message] | None = None,
        *,
        approved_tools: set[str] | None = None,
    ) -> AgentResult:
        """Answer one user message, executing tools as the model requests them.

        ``approved_tools`` carries names the user has explicitly confirmed for
        this turn; CONFIRM_REQUIRED tools outside that set are not executed.
        """
        messages: list[Message] = list(history or [])
        messages.append(Message.user(user_message))
        new_messages: list[Message] = [messages[-1]]
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

            results = await self._execute_calls(
                response.tool_calls, approved_tools or set()
            )
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

        async with anyio.create_task_group() as tg:
            for index, call in enumerate(calls):
                tg.start_soon(run_one, index, call)
        return [r for r in results if r is not None]

    def _execute_one(
        self, call: ToolCall, approved: set[str]
    ) -> tuple[ToolResult, ToolEvent]:
        spec = self._registry.get(call.name)
        permission = spec.permission if spec else PermissionLevel.BLOCKED

        if spec is not None and permission.requires_confirmation():
            if call.name not in approved:
                result = ToolResult(
                    ok=False,
                    tool=call.name,
                    error="confirmation required: ask the user to approve this action",
                    needs_confirmation=True,
                )
                return result, ToolEvent(
                    name=call.name,
                    args=call.args,
                    ok=False,
                    permission=permission,
                    error=result.error,
                    needs_confirmation=True,
                )

        result = self._registry.execute(call.name, call.args)
        return result, ToolEvent(
            name=call.name,
            args=call.args,
            ok=result.ok,
            permission=permission,
            duration_ms=result.duration_ms,
            error=result.error,
        )


def _serialize(result: ToolResult) -> str:
    payload = {"ok": result.ok}
    if result.ok:
        payload["data"] = result.data
    else:
        payload["error"] = result.error
    text = json.dumps(payload, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + '... (truncated)"}'
    return text
