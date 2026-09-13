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


class PendingConfirmation(BaseModel):
    """An action Jarvis will only take once the user says yes."""

    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str
    #: Set when untrusted tool output in the same turn looked like an injection
    #: attempt -- this action may have been suggested by a file, not the user.
    suspicious: bool = False
    #: Set when this action needs approval *only* because untrusted content was
    #: read earlier in the turn (taint escalation), rather than by its own level.
    escalated: bool = False


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    tool_events: list[ToolEvent] = Field(default_factory=list)
    confirmations: list[PendingConfirmation] = Field(default_factory=list)


class ConfirmRequest(BaseModel):
    confirmation_id: str
    approve: bool


class AcknowledgeRequest(BaseModel):
    """Empty list means "everything currently unacknowledged"."""

    alert_ids: list[int] = Field(default_factory=list)


class TranscriptionResponse(BaseModel):
    text: str


class HealthResponse(BaseModel):
    status: str
    version: str
    llm_provider: str
    llm_configured: bool
    tool_count: int
    read_only_mode: bool = False
    #: True when the server can transcribe audio for browsers that cannot.
    transcription: bool = False


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
    #: Visible turns, so the switcher can show which threads have substance.
    message_count: int = 0


class TranscriptResponse(BaseModel):
    conversation_id: str
    messages: list[dict[str, Any]]
