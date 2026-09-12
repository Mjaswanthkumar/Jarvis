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
