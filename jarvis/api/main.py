"""FastAPI application exposing Jarvis to the web UI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from jarvis import __version__
from jarvis.agent import JarvisAgent
from jarvis.api.deps import get_agent, get_store, require_auth
from jarvis.api.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationInfo,
    HealthResponse,
    ToolInfo,
    TranscriptResponse,
)
from jarvis.llm.factory import get_provider
from jarvis.storage import Store
from jarvis.tools import REGISTRY
from jarvis.tools.system import system_health

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@api.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    provider = get_provider()
    return HealthResponse(
        status="online",
        version=__version__,
        llm_provider=provider.name,
        llm_configured=provider.is_configured(),
        tool_count=len(REGISTRY.available()),
    )


@api.get("/system")
def system() -> dict[str, Any]:
    """Live snapshot for the dashboard indicators."""
    return system_health()


@api.get("/tools", response_model=list[ToolInfo])
def tools() -> list[ToolInfo]:
    return [
        ToolInfo(
            name=spec.name,
            description=spec.description,
            permission=spec.permission.value,
            tags=list(spec.tags),
        )
        for spec in REGISTRY.available()
    ]


@api.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    agent: JarvisAgent = Depends(get_agent),
    store: Store = Depends(get_store),
) -> ChatResponse:
    conversation_id = store.ensure_conversation(request.conversation_id)
    history = store.get_messages(conversation_id)

    result = await agent.run(
        request.message,
        history,
        approved_tools=set(request.approved_tools),
    )

    store.add_messages(conversation_id, result.messages)
    store.set_title_if_empty(conversation_id, request.message)
    for event in result.tool_events:
        store.log_tool_call(
            conversation_id,
            event.name,
            event.args,
            event.permission.value,
            event.ok,
            event.error,
            event.duration_ms,
        )

    return ChatResponse(
        reply=result.reply,
        conversation_id=conversation_id,
        tool_events=result.tool_events,
        pending_confirmations=sorted(
            {call.name for call in result.pending_confirmations}
        ),
    )


@api.get("/conversations", response_model=list[ConversationInfo])
def conversations(store: Store = Depends(get_store)) -> list[ConversationInfo]:
    return [ConversationInfo(**row) for row in store.list_conversations()]


@api.get("/conversations/{conversation_id}", response_model=TranscriptResponse)
def transcript(
    conversation_id: str, store: Store = Depends(get_store)
) -> TranscriptResponse:
    messages = store.transcript(conversation_id)
    if not messages and conversation_id not in {
        row["id"] for row in store.list_conversations(limit=100)
    }:
        raise HTTPException(status_code=404, detail="conversation not found")
    return TranscriptResponse(conversation_id=conversation_id, messages=messages)


@api.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, store: Store = Depends(get_store)) -> None:
    store.delete_conversation(conversation_id)


@api.get("/activity")
def activity(limit: int = 20, store: Store = Depends(get_store)) -> list[dict[str, Any]]:
    return store.recent_tool_calls(limit=max(1, min(limit, 100)))


def create_app() -> FastAPI:
    app = FastAPI(
        title="Jarvis",
        version=__version__,
        description="AI-powered personal PC agent",
    )
    app.include_router(api)

    @app.get("/ping", include_in_schema=False)
    def ping() -> dict[str, str]:
        """Unauthenticated liveness probe -- exposes nothing about the machine."""
        return {"status": "ok"}

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()
