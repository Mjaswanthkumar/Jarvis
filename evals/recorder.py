"""Record real model responses once, replay them forever.

Evals that hit the live API cost quota, take minutes and give different answers
each run -- useless in CI. These wrappers record a live run to disk, keyed by the
conversation state, so the same suite replays deterministically and offline.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jarvis.llm.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    ToolDeclaration,
)

CASSETTE_PATH = Path(__file__).parent / "cassette.json"

#: Eval sandboxes live under the machine's temp directory, so their absolute
#: path differs between a laptop and a CI runner. Keys must not depend on it or
#: every sandbox case misses on replay elsewhere.
_SANDBOX_PATH = re.compile(r"[A-Za-z]:[^\"]*?jarvis-evals")


def _key(messages: list[Message], system_prompt: str) -> str:
    """A stable fingerprint of everything that determines the next response."""
    payload = json.dumps(
        {
            "system": system_prompt,
            "messages": [
                {
                    "role": message.role.value,
                    "content": message.content,
                    "tool": message.tool_name,
                    "calls": [
                        {"name": call.name, "args": call.args}
                        for call in message.tool_calls
                    ],
                }
                for message in messages
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(
        _SANDBOX_PATH.sub("{SANDBOX}", payload).encode()
    ).hexdigest()[:20]


class RecordingProvider(LLMProvider):
    """Delegates to a real provider and writes every exchange to a cassette."""

    def __init__(self, inner: LLMProvider, path: Path = CASSETTE_PATH) -> None:
        self._inner = inner
        self._path = path
        self.name = f"recording:{inner.name}"
        self.entries: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    def is_configured(self) -> bool:
        return self._inner.is_configured()

    async def complete(
        self,
        messages: list[Message],
        *,
        system_prompt: str,
        tools: list[ToolDeclaration] | None = None,
    ) -> LLMResponse:
        response = await self._inner.complete(
            messages, system_prompt=system_prompt, tools=tools
        )
        self.entries[_key(messages, system_prompt)] = response.model_dump()
        return response

    def save(self) -> int:
        self._path.write_text(
            json.dumps(self.entries, indent=2, sort_keys=True), encoding="utf-8"
        )
        return len(self.entries)


class ReplayProvider(LLMProvider):
    """Serves recorded responses. Never touches the network."""

    name = "replay"

    def __init__(self, path: Path = CASSETTE_PATH) -> None:
        self._path = path
        if not path.exists():
            raise LLMError(
                f"no cassette at {path}. Record one first: python -m evals --record"
            )
        self.entries: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        self.misses: list[str] = []

    def is_configured(self) -> bool:
        return True

    async def complete(
        self,
        messages: list[Message],
        *,
        system_prompt: str,
        tools: list[ToolDeclaration] | None = None,
    ) -> LLMResponse:
        key = _key(messages, system_prompt)
        entry = self.entries.get(key)
        if entry is None:
            self.misses.append(key)
            raise LLMError(
                "no recorded response for this conversation state -- "
                "the prompt or a tool result changed, so re-record the cassette"
            )
        return LLMResponse(**entry)
