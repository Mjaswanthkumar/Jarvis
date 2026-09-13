"""Watches: standing conditions Jarvis reports on without being asked."""

from __future__ import annotations

from typing import Any

from jarvis import watches
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import tool


def _store() -> Any:
    from jarvis.api.deps import get_store

    return get_store()


@tool(
    description=(
        "Watch a machine metric and tell the user when it crosses a threshold, "
        "without being asked again. Use for 'tell me when my disk drops below "
        "10%', 'let me know if CPU stays above 90%', 'warn me when the battery "
        "gets low'. metric: cpu, memory, disk or battery. comparison: above or "
        "below. threshold: a percentage."
    ),
    permission=PermissionLevel.LOW_RISK,
    tags=("watches",),
)
def create_watch(
    metric: str,
    comparison: str,
    threshold: float,
    note: str = "",
) -> dict[str, Any]:
    metric, direction, value = watches.validate(metric, comparison, threshold)
    row = _store().create_watch(metric, direction.value, value, note.strip()[:120])
    return {
        "watching": f"{metric} {direction.value} {value:g}%",
        "id": row["id"],
        "note": row["note"],
        "checked_every_seconds": 30,
        "message": (
            f"I will tell you when {metric} goes {direction.value} {value:g}%."
        ),
    }


@tool(
    description="List the conditions Jarvis is currently watching for.",
    permission=PermissionLevel.READ_ONLY,
    tags=("watches",),
)
def list_watches() -> dict[str, Any]:
    rows = _store().list_watches()
    return {
        "count": len(rows),
        "watches": [
            {
                "id": row["id"],
                "condition": f"{row['metric']} {row['comparison']} {row['threshold']:g}%",
                "note": row["note"],
                "last_fired_at": row["last_fired_at"],
            }
            for row in rows
        ],
    }


@tool(
    description=(
        "Stop watching a condition. Pass the watch id from list_watches, or the "
        "metric name to remove every watch on that metric."
    ),
    permission=PermissionLevel.LOW_RISK,
    tags=("watches",),
)
def delete_watch(id_or_metric: str) -> dict[str, Any]:
    store = _store()
    target = id_or_metric.strip().lower()

    if store.delete_watch(id_or_metric.strip()):
        return {"removed": 1, "message": "Stopped watching that condition."}

    removed = [
        row["id"] for row in store.list_watches() if row["metric"] == target
    ]
    for watch_id in removed:
        store.delete_watch(watch_id)
    if not removed:
        return {"removed": 0, "message": f"No watch matches '{id_or_metric}'."}
    return {
        "removed": len(removed),
        "message": f"Stopped watching {target} ({len(removed)} condition(s)).",
    }
