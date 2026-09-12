"""FastAPI application exposing Jarvis to the web UI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from jarvis import __version__
from jarvis.agent import AgentResult, JarvisAgent
from jarvis.api.deps import get_agent, get_store, require_auth
from jarvis.api.schemas import (
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    ConversationInfo,
    HealthResponse,
    PendingConfirmation,
    ToolInfo,
    TranscriptResponse,
)
from jarvis.config import get_settings
from jarvis.llm.base import Message, ToolCall
from jarvis.llm.factory import get_provider
from jarvis.security import summarize_call
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
        read_only_mode=get_settings().jarvis_read_only_mode,
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
    store.set_title_if_empty(conversation_id, request.message)
    return _persist_turn(store, conversation_id, result)


@api.post("/confirm", response_model=ChatResponse)
async def confirm(
    request: ConfirmRequest,
    agent: JarvisAgent = Depends(get_agent),
    store: Store = Depends(get_store),
) -> ChatResponse:
    """Approve or deny a pending action, then let Jarvis finish the turn."""
    pending = store.get_confirmation(request.confirmation_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="confirmation not found")
    if pending["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"already {pending['status']}"
        )

    conversation_id = pending["conversation_id"]
    if not store.resolve_confirmation(
        request.confirmation_id, "approved" if request.approve else "denied"
    ):
        raise HTTPException(status_code=409, detail="confirmation already resolved")

    if not request.approve:
        reply = f"Cancelled. I did not run {pending['tool']}."
        store.add_messages(conversation_id, [Message.assistant(reply)])
        return ChatResponse(reply=reply, conversation_id=conversation_id)

    history = store.get_messages(conversation_id)
    result = await agent.resume_with_approval(
        ToolCall(name=pending["tool"], args=pending["args"]), history
    )
    return _persist_turn(store, conversation_id, result)


@api.get("/confirmations", response_model=list[PendingConfirmation])
def confirmations(
    conversation_id: str | None = None, store: Store = Depends(get_store)
) -> list[PendingConfirmation]:
    return [
        PendingConfirmation(**{k: row[k] for k in ("id", "tool", "args", "summary")})
        for row in store.pending_confirmations(conversation_id)
    ]


def _persist_turn(
    store: Store, conversation_id: str, result: AgentResult
) -> ChatResponse:
    """Write messages + audit rows, and register anything awaiting approval."""
    store.add_messages(conversation_id, result.messages)
    for event in result.tool_events:
        store.log_tool_call(
            conversation_id,
            event.name,
            event.args,
            event.permission.value,
            event.ok,
            event.error,
            event.duration_ms,
            event.decision.value,
        )

    pending = [
        store.create_confirmation(
            conversation_id,
            call.name,
            call.args,
            summarize_call(call.name, call.args),
        )
        for call in result.pending_confirmations
    ]
    return ChatResponse(
        reply=result.reply,
        conversation_id=conversation_id,
        tool_events=result.tool_events,
        confirmations=[
            PendingConfirmation(**{k: row[k] for k in ("id", "tool", "args", "summary")})
            for row in pending
        ],
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


#: Only same-origin assets plus inline styles/scripts the page ships itself.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'none'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Jarvis",
        version=__version__,
        description="AI-powered personal PC agent",
    )
    app.include_router(api)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        """Harden the responses -- Jarvis may be served over a plain LAN."""
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
        if request.url.path.startswith("/api"):
            # Machine state is never cacheable, least of all by a shared proxy.
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/ping", include_in_schema=False)
    def ping() -> dict[str, str]:
        """Unauthenticated liveness probe -- exposes nothing about the machine."""
        return {"status": "ok"}

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

        @app.get("/manifest.webmanifest", include_in_schema=False)
        def manifest() -> FileResponse:
            """Served from the root so "Add to Home Screen" scopes correctly."""
            return FileResponse(
                WEB_DIR / "manifest.webmanifest",
                media_type="application/manifest+json",
            )

    return app


app = create_app()
