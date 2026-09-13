"""SQLite-backed conversation history and tool audit log."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.llm.base import Message, Role, ToolCall

logger = logging.getLogger(__name__)

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
    hidden          INTEGER NOT NULL DEFAULT 0,
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
    decision        TEXT NOT NULL DEFAULT 'allow',
    result_preview  TEXT,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirmations (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tool            TEXT NOT NULL,
    args            TEXT NOT NULL DEFAULT '{}',
    summary         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_confirmations_conversation
    ON confirmations(conversation_id, status);
CREATE TABLE IF NOT EXISTS metric_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT NOT NULL,
    cpu_percent     REAL,
    memory_percent  REAL,
    disk_percent    REAL,
    battery_percent REAL
);
CREATE INDEX IF NOT EXISTS idx_metric_samples_time
    ON metric_samples(recorded_at);
CREATE TABLE IF NOT EXISTS watches (
    id           TEXT PRIMARY KEY,
    metric       TEXT NOT NULL,
    comparison   TEXT NOT NULL,
    threshold    REAL NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    streak       INTEGER NOT NULL DEFAULT 0,
    last_fired_at TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id    TEXT,
    metric      TEXT NOT NULL,
    message     TEXT NOT NULL,
    value       REAL NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_unacknowledged
    ON alerts(acknowledged, id);
"""


#: Columns added after the first release. ``CREATE TABLE IF NOT EXISTS`` does
#: nothing to a table that already exists, so an upgraded install keeps the old
#: shape and every insert naming a new column fails at runtime. Each entry is
#: applied with ALTER TABLE when the column is missing.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("messages", "hidden", "INTEGER NOT NULL DEFAULT 0"),
    ("tool_audit", "decision", "TEXT NOT NULL DEFAULT 'allow'"),
    ("tool_audit", "result_preview", "TEXT"),
)


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
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Bring an existing database up to the current column set.

        Called with the lock held. Adding a column is the only migration shape
        used so far; anything structural would need a rebuild-and-copy.
        """
        for table, column, ddl in _MIGRATIONS:
            existing = {
                row["name"]
                for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if not existing:
                continue  # table does not exist yet; the schema script made it
            if column not in existing:
                logger.info("migrating %s: adding column %s", table, column)
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                )

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
                "SELECT c.id, c.title, c.created_at, c.updated_at,"
                " (SELECT COUNT(*) FROM messages m"
                "  WHERE m.conversation_id = c.id AND m.hidden = 0"
                "    AND m.role IN ('user', 'assistant') AND m.content != ''"
                " ) AS message_count"
                " FROM conversations c"
                " ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_title_if_empty(self, conversation_id: str, title: str) -> None:
        """Name a thread after its opening question, trimmed to fit a list."""
        cleaned = " ".join(title.split())
        if len(cleaned) > 60:
            cleaned = cleaned[:57].rstrip(" ,.;:") + "…"
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = ''",
                (cleaned, conversation_id),
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
                int(message.hidden),
                stamp,
            )
            for message in messages
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO messages (conversation_id, role, content, tool_name,"
                " tool_calls, hidden, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
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
                "SELECT role, content, tool_name, tool_calls, hidden FROM messages"
                " WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        messages = [
            Message(
                role=Role(row["role"]),
                content=row["content"],
                tool_name=row["tool_name"],
                tool_calls=[ToolCall(**c) for c in json.loads(row["tool_calls"])],
                hidden=bool(row["hidden"]),
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
                " AND content != '' AND hidden = 0 ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    # -- metrics ----------------------------------------------------------
    def record_metrics(self, sample: dict[str, Any]) -> None:
        """Store one reading of the machine vitals."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO metric_samples (recorded_at, cpu_percent,"
                " memory_percent, disk_percent, battery_percent)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    _now(),
                    sample.get("cpu_percent"),
                    sample.get("memory_percent"),
                    sample.get("disk_percent"),
                    sample.get("battery_percent"),
                ),
            )
            self._conn.commit()

    def metric_history(self, minutes: int = 60, limit: int = 240) -> list[dict[str, Any]]:
        """Samples from the last `minutes`, oldest first."""
        cutoff = (
            datetime.now(tz=timezone.utc) - timedelta(minutes=max(1, minutes))
        ).isoformat(timespec="seconds")
        with self._lock:
            rows = self._conn.execute(
                "SELECT recorded_at, cpu_percent, memory_percent, disk_percent,"
                " battery_percent FROM metric_samples WHERE recorded_at >= ?"
                " ORDER BY recorded_at DESC LIMIT ?",
                (cutoff, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def prune_metrics(self, retention_hours: int) -> int:
        cutoff = (
            datetime.now(tz=timezone.utc) - timedelta(hours=max(1, retention_hours))
        ).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM metric_samples WHERE recorded_at < ?", (cutoff,)
            )
            self._conn.commit()
        return cursor.rowcount

    # -- watches and alerts -----------------------------------------------
    def create_watch(
        self,
        metric: str,
        comparison: str,
        threshold: float,
        note: str = "",
    ) -> dict[str, Any]:
        """Register a condition to be told about. Replaces an identical one."""
        existing = self.find_watch(metric, comparison, threshold)
        if existing:
            return existing

        watch_id = uuid.uuid4().hex[:12]
        row = {
            "id": watch_id,
            "metric": metric,
            "comparison": comparison,
            "threshold": threshold,
            "note": note,
            "streak": 0,
            "last_fired_at": None,
            "created_at": _now(),
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO watches (id, metric, comparison, threshold, note,"
                " streak, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
                (watch_id, metric, comparison, threshold, note, row["created_at"]),
            )
            self._conn.commit()
        return row

    def find_watch(
        self, metric: str, comparison: str, threshold: float
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM watches WHERE metric = ? AND comparison = ?"
                " AND threshold = ?",
                (metric, comparison, threshold),
            ).fetchone()
        return dict(row) if row else None

    def list_watches(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM watches ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_watch(self, watch_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM watches WHERE id = ?", (watch_id,)
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def set_watch_streak(self, watch_id: str, streak: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE watches SET streak = ? WHERE id = ?", (streak, watch_id)
            )
            self._conn.commit()

    def mark_watch_fired(self, watch_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE watches SET last_fired_at = ?, streak = 0 WHERE id = ?",
                (_now(), watch_id),
            )
            self._conn.commit()

    def record_alert(
        self, watch_id: str | None, metric: str, message: str, value: float
    ) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO alerts (watch_id, metric, message, value, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (watch_id, metric, message, value, _now()),
            )
            self._conn.commit()
        return {
            "id": cursor.lastrowid,
            "watch_id": watch_id,
            "metric": metric,
            "message": message,
            "value": value,
            "acknowledged": False,
        }

    def list_alerts(
        self, unacknowledged_only: bool = True, limit: int = 20
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM alerts"
        if unacknowledged_only:
            query += " WHERE acknowledged = 0"
        query += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(query, (max(1, limit),)).fetchall()
        return [
            {**dict(row), "acknowledged": bool(row["acknowledged"])}
            for row in reversed(rows)
        ]

    def acknowledge_alerts(self, alert_ids: list[int] | None = None) -> int:
        with self._lock:
            if alert_ids:
                placeholders = ",".join("?" * len(alert_ids))
                cursor = self._conn.execute(
                    f"UPDATE alerts SET acknowledged = 1 WHERE id IN ({placeholders})",
                    alert_ids,
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE alerts SET acknowledged = 1 WHERE acknowledged = 0"
                )
            self._conn.commit()
        return cursor.rowcount

    # -- confirmations ----------------------------------------------------
    def create_confirmation(
        self,
        conversation_id: str,
        tool: str,
        args: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        """Record an action awaiting the user's approval."""
        confirmation_id = uuid.uuid4().hex[:16]
        row = {
            "id": confirmation_id,
            "conversation_id": conversation_id,
            "tool": tool,
            "args": args,
            "summary": summary,
            "status": "pending",
            "created_at": _now(),
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO confirmations (id, conversation_id, tool, args, summary,"
                " status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (
                    confirmation_id,
                    conversation_id,
                    tool,
                    json.dumps(args, default=str),
                    summary,
                    row["created_at"],
                ),
            )
            self._conn.commit()
        return row

    def get_confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM confirmations WHERE id = ?", (confirmation_id,)
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "args": json.loads(row["args"])}

    def resolve_confirmation(self, confirmation_id: str, status: str) -> bool:
        """Mark a confirmation approved/denied. False if it was already resolved."""
        if status not in ("approved", "denied"):
            raise ValueError("status must be 'approved' or 'denied'")
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE confirmations SET status = ?, resolved_at = ?"
                " WHERE id = ? AND status = 'pending'",
                (status, _now(), confirmation_id),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def pending_confirmations(
        self, conversation_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM confirmations WHERE status = 'pending'"
        params: list[Any] = []
        if conversation_id:
            query += " AND conversation_id = ?"
            params.append(conversation_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [{**dict(row), "args": json.loads(row["args"])} for row in rows]

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
        decision: str = "allow",
        result_preview: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_audit (conversation_id, tool, args, permission,"
                " ok, error, decision, result_preview, duration_ms, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    tool,
                    json.dumps(args, default=str),
                    permission,
                    int(ok),
                    error,
                    decision,
                    result_preview,
                    duration_ms,
                    _now(),
                ),
            )
            self._conn.commit()

    def recent_tool_calls(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool, args, permission, ok, error, decision,"
                " result_preview, duration_ms, created_at"
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
