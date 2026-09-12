"""Request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from jarvis.agent import ToolEvent


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    #: Tool names the user explicitly approved for this turn.
    approved_tools: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    tool_events: list[ToolEvent] = Field(default_factory=list)
    pending_confirmations: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    version: str
    llm_provider: str
    llm_configured: bool
    tool_count: int


class ToolInfo(BaseModel):
    name: str
    description: str
    permission: str
    tags: list[str]


class ConversationInfo(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class TranscriptResponse(BaseModel):
    conversation_id: str
    messages: list[dict[str, Any]]
