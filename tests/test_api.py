from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.api import deps
from jarvis.api.main import create_app
from jarvis.config import get_settings

TOKEN = "test-token"
HEADERS = {"X-Jarvis-Token": TOKEN}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JARVIS_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    deps.get_store.cache_clear()
    deps.get_agent.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    deps.get_store().close()
    deps.get_store.cache_clear()
    get_settings.cache_clear()


def test_ping_needs_no_auth(client: TestClient) -> None:
    assert client.get("/ping").json() == {"status": "ok"}


def test_api_rejects_missing_token(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 401


def test_api_rejects_wrong_token(client: TestClient) -> None:
    response = client.get("/api/health", headers={"X-Jarvis-Token": "nope"})
    assert response.status_code == 401


def test_api_accepts_bearer_token(client: TestClient) -> None:
    response = client.get(
        "/api/health", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200


def test_health_reports_tools(client: TestClient) -> None:
    body = client.get("/api/health", headers=HEADERS).json()
    assert body["status"] == "online"
    assert body["llm_provider"] == "gemini"
    assert body["tool_count"] >= 7


def test_system_endpoint_returns_metrics(client: TestClient) -> None:
    body = client.get("/api/system", headers=HEADERS).json()
    assert body["memory"]["total_gb"] > 0
    assert "percent" in body["cpu"]


def test_tools_endpoint_lists_permissions(client: TestClient) -> None:
    rows = client.get("/api/tools", headers=HEADERS).json()
    assert all(row["permission"] != "BLOCKED" for row in rows)
    assert any(row["name"] == "system_health" for row in rows)


def test_chat_persists_conversation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agent import AgentResult, JarvisAgent

    async def fake_run(self, message, history=None, *, approved_tools=None):
        from jarvis.llm.base import Message

        return AgentResult(
            reply=f"echo: {message}",
            messages=[Message.user(message), Message.assistant(f"echo: {message}")],
        )

    monkeypatch.setattr(JarvisAgent, "run", fake_run)

    first = client.post(
        "/api/chat", headers=HEADERS, json={"message": "hello"}
    ).json()
    conversation_id = first["conversation_id"]
    assert first["reply"] == "echo: hello"

    client.post(
        "/api/chat",
        headers=HEADERS,
        json={"message": "again", "conversation_id": conversation_id},
    )

    transcript = client.get(
        f"/api/conversations/{conversation_id}", headers=HEADERS
    ).json()
    assert [m["content"] for m in transcript["messages"]] == [
        "hello",
        "echo: hello",
        "again",
        "echo: again",
    ]

    conversations = client.get("/api/conversations", headers=HEADERS).json()
    assert conversations[0]["title"] == "hello"

    assert (
        client.delete(
            f"/api/conversations/{conversation_id}", headers=HEADERS
        ).status_code
        == 204
    )


def test_chat_rejects_empty_message(client: TestClient) -> None:
    response = client.post("/api/chat", headers=HEADERS, json={"message": ""})
    assert response.status_code == 422


def test_activity_log_records_tool_calls(client: TestClient) -> None:
    deps.get_store().log_tool_call(
        None, "cpu_info", {}, "READ_ONLY", True, None, 12
    )
    rows = client.get("/api/activity", headers=HEADERS).json()
    assert rows[0]["tool"] == "cpu_info"
    assert rows[0]["ok"] is True


def _stub_agent(monkeypatch: pytest.MonkeyPatch, *, confirm_tool: str) -> None:
    """Make the agent request one CONFIRM_REQUIRED tool, then answer."""
    from jarvis.agent import AgentResult, JarvisAgent, ToolEvent
    from jarvis.llm.base import Message, ToolCall
    from jarvis.security import Decision
    from jarvis.tools.permissions import PermissionLevel

    async def fake_run(self, message, history=None, *, approved_tools=None):
        call = ToolCall(name=confirm_tool, args={"name": "spotify"})
        return AgentResult(
            reply="I need your approval to close Spotify.",
            tool_events=[
                ToolEvent(
                    name=confirm_tool,
                    args=call.args,
                    ok=False,
                    permission=PermissionLevel.CONFIRM_REQUIRED,
                    decision=Decision.CONFIRM,
                    reason="needs confirmation",
                )
            ],
            messages=[Message.user(message), Message.assistant("...")],
            pending_confirmations=[call],
        )

    async def fake_resume(self, call, history=None):
        target = call.args.get("name")
        return AgentResult(
            reply=f"Closed {target}.",
            tool_events=[
                ToolEvent(
                    name=call.name,
                    args=call.args,
                    ok=True,
                    permission=PermissionLevel.CONFIRM_REQUIRED,
                    decision=Decision.ALLOW,
                    reason="approved by the user",
                )
            ],
            messages=[Message.assistant(f"Closed {target}.")],
        )

    monkeypatch.setattr(JarvisAgent, "run", fake_run)
    monkeypatch.setattr(JarvisAgent, "resume_with_approval", fake_resume)


def test_chat_returns_a_pending_confirmation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(monkeypatch, confirm_tool="close_application")
    body = client.post(
        "/api/chat", headers=HEADERS, json={"message": "close spotify"}
    ).json()

    assert len(body["confirmations"]) == 1
    confirmation = body["confirmations"][0]
    assert confirmation["tool"] == "close_application"
    assert confirmation["args"] == {"name": "spotify"}
    assert "close_application" in confirmation["summary"]

    pending = client.get("/api/confirmations", headers=HEADERS).json()
    assert [row["id"] for row in pending] == [confirmation["id"]]


def test_approving_a_confirmation_runs_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(monkeypatch, confirm_tool="close_application")
    chat = client.post(
        "/api/chat", headers=HEADERS, json={"message": "close spotify"}
    ).json()
    confirmation_id = chat["confirmations"][0]["id"]

    body = client.post(
        "/api/confirm",
        headers=HEADERS,
        json={"confirmation_id": confirmation_id, "approve": True},
    ).json()

    assert body["reply"] == "Closed spotify."
    assert body["tool_events"][0]["ok"] is True
    assert client.get("/api/confirmations", headers=HEADERS).json() == []


def test_denying_a_confirmation_runs_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(monkeypatch, confirm_tool="close_application")
    chat = client.post(
        "/api/chat", headers=HEADERS, json={"message": "close spotify"}
    ).json()
    confirmation_id = chat["confirmations"][0]["id"]

    body = client.post(
        "/api/confirm",
        headers=HEADERS,
        json={"confirmation_id": confirmation_id, "approve": False},
    ).json()

    assert "Cancelled" in body["reply"]
    assert body["tool_events"] == []
    assert client.get("/api/confirmations", headers=HEADERS).json() == []


def test_a_confirmation_cannot_be_replayed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An approval token is single-use -- a replayed id must not re-run the tool."""
    _stub_agent(monkeypatch, confirm_tool="close_application")
    chat = client.post(
        "/api/chat", headers=HEADERS, json={"message": "close spotify"}
    ).json()
    payload = {"confirmation_id": chat["confirmations"][0]["id"], "approve": True}

    assert client.post("/api/confirm", headers=HEADERS, json=payload).status_code == 200
    replay = client.post("/api/confirm", headers=HEADERS, json=payload)
    assert replay.status_code == 409


def test_unknown_confirmation_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/api/confirm",
        headers=HEADERS,
        json={"confirmation_id": "does-not-exist", "approve": True},
    )
    assert response.status_code == 404


def test_confirm_endpoint_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/api/confirm", json={"confirmation_id": "x", "approve": True}
    )
    assert response.status_code == 401


def test_audit_log_records_the_policy_decision(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(monkeypatch, confirm_tool="close_application")
    client.post("/api/chat", headers=HEADERS, json={"message": "close spotify"})
    rows = client.get("/api/activity", headers=HEADERS).json()
    assert rows[0]["tool"] == "close_application"
    assert rows[0]["decision"] == "confirm"
    assert rows[0]["ok"] is False


def test_hidden_messages_stay_out_of_the_transcript(client: TestClient) -> None:
    from jarvis.api import deps
    from jarvis.llm.base import Message

    store = deps.get_store()
    conversation_id = store.create_conversation()
    store.add_messages(
        conversation_id,
        [
            Message.user("visible question"),
            Message.user("[jarvis] internal note", hidden=True),
            Message.assistant("visible answer"),
        ],
    )
    body = client.get(f"/api/conversations/{conversation_id}", headers=HEADERS).json()
    assert [m["content"] for m in body["messages"]] == [
        "visible question",
        "visible answer",
    ]


def test_security_headers_are_set_on_the_page(client: TestClient) -> None:
    response = client.get("/ping")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_api_responses_are_never_cached(client: TestClient) -> None:
    """Machine state must not sit in a phone or proxy cache."""
    response = client.get("/api/system", headers=HEADERS)
    assert response.headers["Cache-Control"] == "no-store"


def test_manifest_is_served_for_home_screen_install(client: TestClient) -> None:
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    body = response.json()
    assert body["display"] == "standalone"
    assert body["start_url"] == "/"


def test_repeated_bad_tokens_lock_the_client_out(client: TestClient) -> None:
    from jarvis.api.deps import MAX_FAILURES, THROTTLE

    THROTTLE.reset()
    try:
        for _ in range(MAX_FAILURES):
            assert (
                client.get("/api/health", headers={"X-Jarvis-Token": "wrong"}).status_code
                == 401
            )

        locked = client.get("/api/health", headers={"X-Jarvis-Token": "wrong"})
        assert locked.status_code == 429
        assert "Retry-After" in locked.headers

        # A valid token is refused too while the lockout stands.
        assert client.get("/api/health", headers=HEADERS).status_code == 429
    finally:
        THROTTLE.reset()


def test_a_successful_login_clears_earlier_failures(client: TestClient) -> None:
    from jarvis.api.deps import MAX_FAILURES, THROTTLE

    THROTTLE.reset()
    try:
        for _ in range(MAX_FAILURES - 1):
            client.get("/api/health", headers={"X-Jarvis-Token": "wrong"})
        assert client.get("/api/health", headers=HEADERS).status_code == 200

        for _ in range(MAX_FAILURES - 1):
            assert (
                client.get("/api/health", headers={"X-Jarvis-Token": "wrong"}).status_code
                == 401
            )
    finally:
        THROTTLE.reset()


def _stub_transcription(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    from jarvis.llm.gemini import GeminiProvider

    async def fake_transcribe(self, audio: bytes, mime_type: str) -> str:
        assert audio and mime_type
        return text

    monkeypatch.setattr(GeminiProvider, "supports_transcription", True)
    monkeypatch.setattr(GeminiProvider, "transcribe", fake_transcribe)


def test_transcribe_returns_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_transcription(monkeypatch, "  open notepad  ")
    response = client.post(
        "/api/transcribe",
        headers=HEADERS,
        files={"audio": ("speech.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "open notepad"}


def test_transcribe_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/api/transcribe",
        files={"audio": ("speech.webm", b"x", "audio/webm")},
    )
    assert response.status_code == 401


def test_transcribe_rejects_non_audio(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upload endpoint must not accept whatever a caller feels like sending."""
    _stub_transcription(monkeypatch, "x")
    response = client.post(
        "/api/transcribe",
        headers=HEADERS,
        files={"audio": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_transcribe_accepts_a_codec_parameter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_transcription(monkeypatch, "hello")
    response = client.post(
        "/api/transcribe",
        headers=HEADERS,
        files={"audio": ("s.webm", b"bytes", "audio/webm;codecs=opus")},
    )
    assert response.status_code == 200


def test_transcribe_rejects_an_empty_recording(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_transcription(monkeypatch, "x")
    response = client.post(
        "/api/transcribe",
        headers=HEADERS,
        files={"audio": ("s.webm", b"", "audio/webm")},
    )
    assert response.status_code == 400


def test_transcribe_rejects_oversized_audio(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.api.main import _MAX_AUDIO_BYTES

    _stub_transcription(monkeypatch, "x")
    response = client.post(
        "/api/transcribe",
        headers=HEADERS,
        files={"audio": ("s.webm", b"0" * (_MAX_AUDIO_BYTES + 10), "audio/webm")},
    )
    assert response.status_code == 413


def test_transcribe_reports_provider_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.llm.base import LLMError
    from jarvis.llm.gemini import GeminiProvider

    async def failing(self, audio: bytes, mime_type: str) -> str:
        raise LLMError("Gemini rate limit reached for this model.")

    monkeypatch.setattr(GeminiProvider, "supports_transcription", True)
    monkeypatch.setattr(GeminiProvider, "transcribe", failing)

    response = client.post(
        "/api/transcribe",
        headers=HEADERS,
        files={"audio": ("s.webm", b"bytes", "audio/webm")},
    )
    assert response.status_code == 502
    assert "rate limit" in response.json()["detail"]


def test_health_advertises_transcription_support(client: TestClient) -> None:
    assert "transcription" in client.get("/api/health", headers=HEADERS).json()


# ------------------------------------------------- conversation management ----
def test_new_conversation_returns_an_empty_thread(client: TestClient) -> None:
    """Without this, every topic accretes into one silently-truncated thread."""
    body = client.post("/api/conversations", headers=HEADERS).json()
    assert body["id"]
    assert body["message_count"] == 0
    assert body["title"] == ""


def test_new_conversation_requires_auth(client: TestClient) -> None:
    assert client.post("/api/conversations").status_code == 401


def test_conversations_report_visible_message_counts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count drives the switcher, so hidden and tool turns must not inflate it."""
    from jarvis.api import deps
    from jarvis.llm.base import Message

    store = deps.get_store()
    conversation_id = store.create_conversation()
    store.add_messages(
        conversation_id,
        [
            Message.user("visible question"),
            Message.assistant("visible answer"),
            Message.user("[jarvis] internal", hidden=True),
            Message.tool("cpu_info", "{}"),
        ],
    )
    row = next(
        r
        for r in client.get("/api/conversations", headers=HEADERS).json()
        if r["id"] == conversation_id
    )
    assert row["message_count"] == 2


def test_conversations_are_ordered_most_recent_first(client: TestClient) -> None:
    from jarvis.api import deps

    store = deps.get_store()
    older = store.create_conversation()
    newer = store.create_conversation()
    ids = [r["id"] for r in client.get("/api/conversations", headers=HEADERS).json()]
    assert ids.index(newer) < ids.index(older)


def test_long_first_messages_become_readable_titles(client: TestClient) -> None:
    """A raw 4000-character message would make the switcher unusable."""
    from jarvis.api import deps

    store = deps.get_store()
    conversation_id = store.create_conversation()
    store.set_title_if_empty(
        conversation_id,
        "Please tell me in considerable detail   which of my running processes "
        "are consuming the most memory right now and why that might be",
    )
    row = next(
        r
        for r in client.get("/api/conversations", headers=HEADERS).json()
        if r["id"] == conversation_id
    )
    assert len(row["title"]) <= 60
    assert row["title"].endswith("…")
    assert "  " not in row["title"]


def test_a_title_is_only_set_once(client: TestClient) -> None:
    from jarvis.api import deps

    store = deps.get_store()
    conversation_id = store.create_conversation()
    store.set_title_if_empty(conversation_id, "first question")
    store.set_title_if_empty(conversation_id, "second question")
    row = next(
        r
        for r in client.get("/api/conversations", headers=HEADERS).json()
        if r["id"] == conversation_id
    )
    assert row["title"] == "first question"


def test_conversation_limit_is_clamped(client: TestClient) -> None:
    assert client.get("/api/conversations?limit=99999", headers=HEADERS).status_code == 200
    assert client.get("/api/conversations?limit=0", headers=HEADERS).status_code == 200


def test_tools_endpoint_gives_the_ui_what_it_needs_to_explain_itself(
    client: TestClient,
) -> None:
    """The capability sheet groups by first tag and labels by permission."""
    rows = client.get("/api/tools", headers=HEADERS).json()
    assert len(rows) >= 28
    for row in rows:
        assert row["tags"], f"{row['name']} has no tag to group under"
        assert row["permission"] in {"READ_ONLY", "LOW_RISK", "CONFIRM_REQUIRED"}
        assert len(row["description"]) > 20


def test_tool_events_carry_a_result_preview(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI shows the data an answer came from, so it has to be returned."""
    from jarvis.agent import AgentResult, JarvisAgent, ToolEvent
    from jarvis.llm.base import Message
    from jarvis.tools.permissions import PermissionLevel

    async def fake_run(self, message, history=None, *, approved_tools=None):
        return AgentResult(
            reply="15.7 GB total",
            tool_events=[
                ToolEvent(
                    name="memory_info",
                    ok=True,
                    permission=PermissionLevel.READ_ONLY,
                    duration_ms=12,
                    result_preview='{\n  "total_gb": 15.71\n}',
                )
            ],
            messages=[Message.user(message), Message.assistant("15.7 GB total")],
        )

    monkeypatch.setattr(JarvisAgent, "run", fake_run)
    body = client.post("/api/chat", headers=HEADERS, json={"message": "ram?"}).json()
    assert "15.71" in body["tool_events"][0]["result_preview"]

    row = client.get("/api/activity", headers=HEADERS).json()[0]
    assert "15.71" in row["result_preview"]


def test_previews_are_truncated(client: TestClient) -> None:
    """A 200-line file must not be dumped into the chat."""
    from jarvis.agent import MAX_PREVIEW_CHARS, _preview
    from jarvis.tools.registry import ToolResult

    result = ToolResult(ok=True, tool="t", data={"content": "x" * 50_000})
    preview = _preview(result)
    assert preview is not None
    assert len(preview) < MAX_PREVIEW_CHARS + 40
    assert preview.endswith("(truncated)")


def test_failed_tools_have_no_preview() -> None:
    from jarvis.agent import _preview
    from jarvis.tools.registry import ToolResult

    assert _preview(ToolResult(ok=False, tool="t", error="boom")) is None


def test_an_existing_database_is_migrated_in_place(tmp_path) -> None:
    """CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    so every column added after the first release broke upgrades until now."""
    import sqlite3

    from jarvis.storage import Store

    database = tmp_path / "old.db"
    legacy = sqlite3.connect(database)
    legacy.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
            tool_name TEXT, tool_calls TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL);
        CREATE TABLE tool_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT,
            tool TEXT NOT NULL, args TEXT NOT NULL DEFAULT '{}',
            permission TEXT NOT NULL, ok INTEGER NOT NULL, error TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        """
    )
    legacy.execute(
        "INSERT INTO conversations VALUES ('old1', 'kept', '2026-01-01', '2026-01-01')"
    )
    legacy.commit()
    legacy.close()

    store = Store(database)
    try:
        # The pre-existing row survives the migration.
        assert [row["id"] for row in store.list_conversations()] == ["old1"]

        # And the columns added since the first release now work.
        from jarvis.llm.base import Message

        store.add_messages("old1", [Message.user("hi", hidden=True)])
        store.log_tool_call(
            "old1", "cpu_info", {}, "READ_ONLY", True, None, 5, "allow", "{}"
        )
        assert store.recent_tool_calls()[0]["result_preview"] == "{}"
        assert store.transcript("old1") == []  # the hidden message stays hidden
    finally:
        store.close()


def test_migration_is_idempotent(tmp_path) -> None:
    from jarvis.storage import Store

    database = tmp_path / "twice.db"
    Store(database).close()
    store = Store(database)  # must not fail with "duplicate column name"
    store.close()


def test_confirmations_can_be_filtered_by_conversation(client: TestClient) -> None:
    """The UI restores unanswered approvals after a reload, per conversation."""
    from jarvis.api import deps

    store = deps.get_store()
    first = store.create_conversation()
    second = store.create_conversation()
    store.create_confirmation(first, "close_application", {"name": "a"}, "Run a")
    store.create_confirmation(second, "close_application", {"name": "b"}, "Run b")

    mine = client.get(
        f"/api/confirmations?conversation_id={first}", headers=HEADERS
    ).json()
    assert [row["args"]["name"] for row in mine] == ["a"]

    everything = client.get("/api/confirmations", headers=HEADERS).json()
    assert len(everything) >= 2


def test_resolved_confirmations_are_not_restored(client: TestClient) -> None:
    from jarvis.api import deps

    store = deps.get_store()
    conversation = store.create_conversation()
    row = store.create_confirmation(conversation, "close_application", {}, "Run")
    store.resolve_confirmation(row["id"], "denied")

    pending = client.get(
        f"/api/confirmations?conversation_id={conversation}", headers=HEADERS
    ).json()
    assert pending == []
