"""Provider-neutral chat/tool-calling interfaces.

Swapping Gemini for another backend means implementing :class:`LLMProvider`
only -- the agent, tools and API never import a vendor SDK.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

ToolDeclaration = dict[str, Any]


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """A tool the model asked to run."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """One turn of conversation, including tool results."""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    #: Set on Role.TOOL messages -- the name of the tool that produced content.
    tool_name: str | None = None

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(
        cls, content: str = "", tool_calls: list[ToolCall] | None = None
    ) -> "Message":
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, tool_name: str, content: str) -> "Message":
        return cls(role=Role.TOOL, tool_name=tool_name, content=content)


class LLMResponse(BaseModel):
    """What a provider returned for one request."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str = ""
    raw_usage: dict[str, Any] = Field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMError(RuntimeError):
    """Raised when a provider call fails or is misconfigured."""


class LLMProvider(ABC):
    """Minimal contract every backend must satisfy."""

    name: str = "unknown"

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        system_prompt: str,
        tools: list[ToolDeclaration] | None = None,
    ) -> LLMResponse:
        """Return the next assistant turn, possibly requesting tool calls."""

    @abstractmethod
    def is_configured(self) -> bool:
        """True when credentials are present and the provider can be used."""
