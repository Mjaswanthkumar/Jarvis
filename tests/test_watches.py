"""Tests for proactive watches: the agent noticing without being asked."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from jarvis import watches
from jarvis.metrics import MetricSampler, _in_cooldown
from jarvis.storage import Store
from jarvis.tools import REGISTRY
from jarvis.tools.permissions import PermissionLevel


# ---------------------------------------------------------------- pure ----
@pytest.mark.parametrize(
    "comparison,value,threshold,expected",
    [
        ("above", 95.0, 90.0, True),
        ("above", 85.0, 90.0, False),
        ("above", 90.0, 90.0, False),  # strictly above
        ("below", 5.0, 10.0, True),
        ("below", 15.0, 10.0, False),
    ],
)
def test_comparisons(comparison, value, threshold, expected) -> None:
    assert watches.Comparison(comparison).breached(value, threshold) is expected


def test_validate_normalises_input() -> None:
    metric, comparison, threshold = watches.validate(" DISK ", " Below ", 10)
    assert (metric, comparison.value, threshold) == ("disk", "below", 10.0)


@pytest.mark.parametrize(
    "metric,comparison,threshold",
    [
        ("temperature", "above", 50),
        ("cpu", "sideways", 50),
        ("cpu", "above", 150),
        ("cpu", "above", -1),
    ],
)
def test_validate_rejects_nonsense(metric, comparison, threshold) -> None:
    with pytest.raises(ValueError):
        watches.validate(metric, comparison, threshold)


def test_evaluate_finds_only_breaching_watches() -> None:
    defined = [
        {"id": "a", "metric": "cpu", "comparison": "above", "threshold": 90.0},
        {"id": "b", "metric": "memory", "comparison": "above", "threshold": 90.0},
        {"id": "c", "metric": "disk", "comparison": "below", "threshold": 10.0},
    ]
    sample = {"cpu_percent": 95.0, "memory_percent": 40.0, "disk_percent": 50.0}
    fired = watches.evaluate(defined, sample)
    assert [w["id"] for w, _ in fired] == ["a"]
    assert fired[0][1] == 95.0


def test_evaluate_skips_metrics_with_no_reading() -> None:
    """A desktop has no battery; a watch on it must not fire on None."""
    defined = [
        {"id": "b", "metric": "battery", "comparison": "below", "threshold": 20.0}
    ]
    assert watches.evaluate(defined, {"battery_percent": None}) == []


def test_alert_text_is_specific() -> None:
    text = watches.alert_text(
        {"metric": "disk", "comparison": "below", "threshold": 10.0, "note": "SSD"},
        7.4,
    )
    assert "Disk" in text and "dropped below 10%" in text and "7%" in text
    assert "SSD" in text


def test_cooldown_window() -> None:
    now = datetime.now(tz=timezone.utc)
    assert _in_cooldown(None, now) is False
    recent = (now - timedelta(minutes=5)).isoformat()
    assert _in_cooldown(recent, now) is True
    old = (now - timedelta(minutes=watches.COOLDOWN_MINUTES + 1)).isoformat()
    assert _in_cooldown(old, now) is False


# ------------------------------------------------------------- storage ----
def test_identical_watches_are_deduplicated(store: Store) -> None:
    """Asking twice should not produce two alerts for one condition."""
    first = store.create_watch("disk", "below", 10.0)
    second = store.create_watch("disk", "below", 10.0)
    assert first["id"] == second["id"]
    assert len(store.list_watches()) == 1


def test_alerts_are_acknowledged(store: Store) -> None:
    watch = store.create_watch("cpu", "above", 90.0)
    store.record_alert(watch["id"], "cpu", "CPU high", 95.0)
    assert len(store.list_alerts()) == 1
    assert store.acknowledge_alerts() == 1
    assert store.list_alerts() == []
    assert len(store.list_alerts(unacknowledged_only=False)) == 1


# ----------------------------------------------------------- the sampler ----
@pytest.mark.asyncio
async def test_a_watch_needs_consecutive_breaches_before_firing(
    store: Store,
) -> None:
    """One spike is not a problem; a sustained condition is."""
    store.create_watch("cpu", "above", 0.0)
    sampler = MetricSampler(store, interval=0.05)

    sampler._check_watches({"cpu_percent": 50.0})
    assert store.list_alerts() == [], "fired on the very first sample"

    sampler._check_watches({"cpu_percent": 50.0})
    assert len(store.list_alerts()) == 1


@pytest.mark.asyncio
async def test_a_recovered_metric_resets_the_streak(store: Store) -> None:
    store.create_watch("cpu", "above", 80.0)
    sampler = MetricSampler(store, interval=0.05)

    sampler._check_watches({"cpu_percent": 90.0})   # streak 1
    sampler._check_watches({"cpu_percent": 10.0})   # recovered, reset
    sampler._check_watches({"cpu_percent": 90.0})   # streak 1 again
    assert store.list_alerts() == []


@pytest.mark.asyncio
async def test_cooldown_prevents_alert_spam(store: Store) -> None:
    """A metric hovering on its threshold must not alert every 30 seconds."""
    store.create_watch("cpu", "above", 0.0)
    sampler = MetricSampler(store, interval=0.05)

    for _ in range(10):
        sampler._check_watches({"cpu_percent": 50.0})
    assert len(store.list_alerts()) == 1


@pytest.mark.asyncio
async def test_the_sampler_raises_alerts_end_to_end(store: Store) -> None:
    # Memory is always above 0%, whereas CPU can legitimately read exactly 0.0
    # and the comparison is strictly above -- which made this flaky.
    store.create_watch("memory", "above", 0.0, "any usage at all")
    sampler = MetricSampler(store, interval=0.05)
    sampler.start()
    try:
        await asyncio.sleep(0.3)
    finally:
        await sampler.stop()

    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert "Memory has risen above 0%" in alerts[0]["message"]
    assert "any usage at all" in alerts[0]["message"]


@pytest.mark.asyncio
async def test_no_watches_means_no_work(store: Store) -> None:
    sampler = MetricSampler(store, interval=0.05)
    sampler._check_watches({"cpu_percent": 99.0})
    assert store.list_alerts() == []


# ------------------------------------------------------------- the tools ----
def test_create_watch_tool(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.api import deps

    monkeypatch.setattr(deps, "get_store", lambda: store)
    result = REGISTRY.execute(
        "create_watch",
        {"metric": "disk", "comparison": "below", "threshold": 10, "note": "low"},
    )
    assert result.ok, result.error
    assert result.data["watching"] == "disk below 10%"
    assert len(store.list_watches()) == 1


def test_create_watch_rejects_a_bad_metric(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.api import deps

    monkeypatch.setattr(deps, "get_store", lambda: store)
    result = REGISTRY.execute(
        "create_watch", {"metric": "gpu", "comparison": "above", "threshold": 90}
    )
    assert not result.ok
    assert "must be one of" in (result.error or "")


def test_list_and_delete_watch_tools(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.api import deps

    monkeypatch.setattr(deps, "get_store", lambda: store)
    REGISTRY.execute(
        "create_watch", {"metric": "cpu", "comparison": "above", "threshold": 90}
    )
    listed = REGISTRY.execute("list_watches")
    assert listed.data["count"] == 1
    assert listed.data["watches"][0]["condition"] == "cpu above 90%"

    removed = REGISTRY.execute("delete_watch", {"id_or_metric": "cpu"})
    assert removed.data["removed"] == 1
    assert store.list_watches() == []


def test_delete_watch_reports_a_miss(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.api import deps

    monkeypatch.setattr(deps, "get_store", lambda: store)
    result = REGISTRY.execute("delete_watch", {"id_or_metric": "nothing"})
    assert result.ok
    assert result.data["removed"] == 0


def test_watch_tools_have_sensible_permissions() -> None:
    """Creating a watch changes standing behaviour, so it is not READ_ONLY."""
    assert REGISTRY.get("create_watch").permission is PermissionLevel.LOW_RISK
    assert REGISTRY.get("delete_watch").permission is PermissionLevel.LOW_RISK
    assert REGISTRY.get("list_watches").permission is PermissionLevel.READ_ONLY


# ------------------------------------------------------- desktop alerts ----
def test_notification_content_is_never_interpolated_into_the_script() -> None:
    """Alert text reaches PowerShell through the environment, not the source.

    A watch note is user-supplied, so a message that looks like a command must
    stay a message.
    """
    from jarvis import notify

    assert "$env:JARVIS_TOAST_BODY" in notify._SCRIPT
    assert "{body}" not in notify._SCRIPT
    assert "{title}" not in notify._SCRIPT


def test_notifications_can_be_disabled() -> None:
    from jarvis import notify

    assert notify.send("Jarvis", "anything", enabled=False) is False


def test_empty_notifications_are_skipped() -> None:
    from jarvis import notify

    assert notify.send("Jarvis", "   ") is False


def test_a_failing_notifier_does_not_break_a_watch(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing PowerShell must not stop the alert being recorded."""
    from jarvis import notify

    def boom(*args, **kwargs):
        raise OSError("powershell is not installed")

    monkeypatch.setattr(notify.subprocess, "run", boom)
    store.create_watch("cpu", "above", 0.0)
    sampler = MetricSampler(store, interval=0.05)

    sampler._check_watches({"cpu_percent": 50.0})
    sampler._check_watches({"cpu_percent": 50.0})

    assert len(store.list_alerts()) == 1, "the alert was lost when the toast failed"


def test_the_sampler_notifies_the_desktop_when_a_watch_fires(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis import notify

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notify, "send", lambda title, body, **kw: sent.append((title, body)) or True
    )

    store.create_watch("disk", "below", 50.0, "nearly full")
    sampler = MetricSampler(store, interval=0.05)
    sampler._check_watches({"disk_percent": 5.0})
    sampler._check_watches({"disk_percent": 5.0})

    assert len(sent) == 1
    assert sent[0][0] == "Jarvis"
    assert "dropped below 50%" in sent[0][1]


def test_desktop_notifications_respect_the_setting(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis import notify

    calls: list[bool] = []
    monkeypatch.setattr(
        notify, "send", lambda title, body, **kw: calls.append(kw.get("enabled", True))
    )

    store.create_watch("cpu", "above", 0.0)
    sampler = MetricSampler(store, interval=0.05, desktop_notifications=False)
    sampler._check_watches({"cpu_percent": 50.0})
    sampler._check_watches({"cpu_percent": 50.0})

    assert calls == [False]
