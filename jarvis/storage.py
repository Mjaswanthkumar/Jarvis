"""SQLite-backed conversation history and tool audit log."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis.llm.base import Message, Role, ToolCall

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    tool_name       TEXT,
    tool_calls      TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS tool_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    tool            TEXT NOT NULL,
    args            TEXT NOT NULL DEFAULT '{}',
    permission      TEXT NOT NULL,
    ok              INTEGER NOT NULL,
    error           TEXT,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class Store:
    """Small synchronous SQLite wrapper; safe to share across threads."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- conversations ----------------------------------------------------
    def create_conversation(self, title: str = "") -> str:
        conversation_id = uuid.uuid4().hex[:16]
        stamp = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (conversation_id, title, stamp, stamp),
            )
            self._conn.commit()
        return conversation_id

    def ensure_conversation(self, conversation_id: str | None) -> str:
        """Return an existing conversation id, creating the row when needed."""
        if not conversation_id:
            return self.create_conversation()
        stamp = _now()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO conversations (id, title, created_at,"
                " updated_at) VALUES (?, '', ?, ?)",
                (conversation_id, stamp, stamp),
            )
            self._conn.commit()
        return conversation_id

    def list_conversations(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations"
                " ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_title_if_empty(self, conversation_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = ''",
                (title[:80], conversation_id),
            )
            self._conn.commit()

    def delete_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM messages WHERE conversation_id = ?", (conversation_id,)
            )
            self._conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            self._conn.commit()

    # -- messages ---------------------------------------------------------
    def add_messages(self, conversation_id: str, messages: list[Message]) -> None:
        stamp = _now()
        rows = [
            (
                conversation_id,
                message.role.value,
                message.content,
                message.tool_name,
                json.dumps([call.model_dump() for call in message.tool_calls]),
                stamp,
            )
            for message in messages
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO messages (conversation_id, role, content, tool_name,"
                " tool_calls, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (stamp, conversation_id),
            )
            self._conn.commit()

    def get_messages(self, conversation_id: str, limit: int = 40) -> list[Message]:
        """Return the last ``limit`` messages in chronological order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, tool_name, tool_calls FROM messages"
                " WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        messages = [
            Message(
                role=Role(row["role"]),
                content=row["content"],
                tool_name=row["tool_name"],
                tool_calls=[ToolCall(**c) for c in json.loads(row["tool_calls"])],
            )
            for row in reversed(rows)
        ]
        return _trim_dangling_tool_messages(messages)

    def transcript(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """User/assistant turns only -- what the chat UI renders."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, created_at FROM messages"
                " WHERE conversation_id = ? AND role IN ('user', 'assistant')"
                " AND content != '' ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    # -- audit ------------------------------------------------------------
    def log_tool_call(
        self,
        conversation_id: str | None,
        tool: str,
        args: dict[str, Any],
        permission: str,
        ok: bool,
        error: str | None,
        duration_ms: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_audit (conversation_id, tool, args, permission, ok,"
                " error, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    tool,
                    json.dumps(args, default=str),
                    permission,
                    int(ok),
                    error,
                    duration_ms,
                    _now(),
                ),
            )
            self._conn.commit()

    def recent_tool_calls(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool, args, permission, ok, error, duration_ms, created_at"
                " FROM tool_audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {**dict(row), "ok": bool(row["ok"]), "args": json.loads(row["args"])}
            for row in rows
        ]


def _trim_dangling_tool_messages(messages: list[Message]) -> list[Message]:
    """Drop leading tool messages whose originating assistant turn was cut off.

    A history window that starts with a function response confuses the provider.
    """
    index = 0
    while index < len(messages) and messages[index].role is Role.TOOL:
        index += 1
    return messages[index:]
