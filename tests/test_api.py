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
