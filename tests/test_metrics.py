"""Tests for metric sampling, history and the trend tool."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jarvis.metrics import MetricSampler, take_sample
from jarvis.storage import Store


def test_a_sample_reads_the_real_machine() -> None:
    sample = take_sample()
    assert 0 <= sample["cpu_percent"] <= 100
    assert 0 < sample["memory_percent"] <= 100
    assert sample["disk_percent"] is None or 0 <= sample["disk_percent"] <= 100


def test_samples_round_trip(store: Store) -> None:
    store.record_metrics(
        {"cpu_percent": 12.5, "memory_percent": 60.0, "disk_percent": 40.0,
         "battery_percent": 99.0}
    )
    rows = store.metric_history(minutes=60)
    assert len(rows) == 1
    assert rows[0]["cpu_percent"] == 12.5
    assert rows[0]["battery_percent"] == 99.0


def test_history_is_oldest_first(store: Store) -> None:
    """Sparklines draw left to right, so order is part of the contract."""
    for value in (10.0, 20.0, 30.0):
        store.record_metrics({"cpu_percent": value, "memory_percent": 50.0})
    assert [r["cpu_percent"] for r in store.metric_history()] == [10.0, 20.0, 30.0]


def test_history_excludes_samples_outside_the_window(store: Store) -> None:
    old = (datetime.now(tz=timezone.utc) - timedelta(hours=5)).isoformat(
        timespec="seconds"
    )
    with store._lock:  # inserting at an explicit time needs raw access
        store._conn.execute(
            "INSERT INTO metric_samples (recorded_at, cpu_percent) VALUES (?, ?)",
            (old, 99.0),
        )
        store._conn.commit()
    store.record_metrics({"cpu_percent": 11.0})

    recent = store.metric_history(minutes=60)
    assert [r["cpu_percent"] for r in recent] == [11.0]


def test_pruning_removes_only_old_samples(store: Store) -> None:
    old = (datetime.now(tz=timezone.utc) - timedelta(hours=100)).isoformat(
        timespec="seconds"
    )
    with store._lock:
        store._conn.execute(
            "INSERT INTO metric_samples (recorded_at, cpu_percent) VALUES (?, ?)",
            (old, 5.0),
        )
        store._conn.commit()
    store.record_metrics({"cpu_percent": 50.0})

    assert store.prune_metrics(retention_hours=48) == 1
    assert [r["cpu_percent"] for r in store.metric_history(minutes=60)] == [50.0]


def test_history_limit_is_respected(store: Store) -> None:
    for value in range(10):
        store.record_metrics({"cpu_percent": float(value)})
    assert len(store.metric_history(minutes=60, limit=3)) == 3


# ------------------------------------------------------------- the tool ----
def _seed(store: Store, values: list[float]) -> None:
    for value in values:
        store.record_metrics({"cpu_percent": value, "memory_percent": 50.0})


def test_metric_history_tool_summarises_a_trend(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.api import deps
    from jarvis.tools import REGISTRY

    _seed(store, [10.0, 12.0, 40.0, 60.0])
    monkeypatch.setattr(deps, "get_store", lambda: store)

    result = REGISTRY.execute("metric_history", {"metric": "cpu", "minutes": 60})
    assert result.ok, result.error
    assert result.data["samples"] == 4
    assert result.data["peak_percent"] == 60.0
    assert result.data["low_percent"] == 10.0
    assert result.data["trend"] == "rising"


def test_metric_history_tool_detects_a_falling_trend(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.api import deps
    from jarvis.tools import REGISTRY

    _seed(store, [80.0, 75.0, 20.0, 10.0])
    monkeypatch.setattr(deps, "get_store", lambda: store)
    result = REGISTRY.execute("metric_history", {"metric": "cpu"})
    assert result.data["trend"] == "falling"


def test_metric_history_tool_reports_steady(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.api import deps
    from jarvis.tools import REGISTRY

    _seed(store, [50.0, 51.0, 49.0, 50.0])
    monkeypatch.setattr(deps, "get_store", lambda: store)
    assert REGISTRY.execute("metric_history", {"metric": "cpu"}).data["trend"] == "steady"


def test_metric_history_tool_says_so_when_there_is_no_data(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Better an explicit 'not enough history' than a confidently empty answer."""
    from jarvis.api import deps
    from jarvis.tools import REGISTRY

    monkeypatch.setattr(deps, "get_store", lambda: store)
    result = REGISTRY.execute("metric_history", {"metric": "cpu"})
    assert result.ok
    assert result.data["samples"] == 0
    assert "No history yet" in result.data["message"]


def test_metric_history_tool_rejects_an_unknown_metric() -> None:
    from jarvis.tools import REGISTRY

    result = REGISTRY.execute("metric_history", {"metric": "temperature"})
    assert not result.ok
    assert "must be one of" in (result.error or "")


def test_metric_history_tool_is_read_only() -> None:
    from jarvis.tools import REGISTRY
    from jarvis.tools.permissions import PermissionLevel

    assert REGISTRY.get("metric_history").permission is PermissionLevel.READ_ONLY


# ---------------------------------------------------------- the sampler ----
@pytest.mark.asyncio
async def test_sampler_records_on_its_interval(store: Store) -> None:
    sampler = MetricSampler(store, interval=0.05)
    sampler.start()
    try:
        import asyncio

        await asyncio.sleep(0.2)
    finally:
        await sampler.stop()
    assert len(store.metric_history(minutes=60)) >= 2


@pytest.mark.asyncio
async def test_a_failing_sample_does_not_kill_the_loop(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    calls = {"n": 0}

    def flaky(sample: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk busy")

    monkeypatch.setattr(store, "record_metrics", flaky)
    sampler = MetricSampler(store, interval=0.05)
    sampler.start()
    try:
        await asyncio.sleep(0.25)
    finally:
        await sampler.stop()
    assert calls["n"] >= 3, "the loop stopped after one failure"


@pytest.mark.asyncio
async def test_stopping_an_unstarted_sampler_is_safe(store: Store) -> None:
    await MetricSampler(store).stop()
