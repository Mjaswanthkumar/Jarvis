"""Background sampling of machine vitals, so trends can be answered.

An instantaneous number cannot answer "is my CPU spiking?" or "was it busy an
hour ago?" -- by the time a user thinks to look, the spike is over. A small
sampler records CPU, memory, disk and battery on an interval; the UI draws
sparklines from it and the agent gets a tool that can read it back.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import psutil

logger = logging.getLogger(__name__)

#: How often a sample is taken. Frequent enough to show a spike, cheap enough to
#: run forever: one row is ~40 bytes, so a day costs well under a megabyte.
SAMPLE_INTERVAL_SECONDS = 30.0

#: Samples older than this are pruned. Long enough for "today", short enough
#: that the table never needs thinking about.
RETENTION_HOURS = 48


def take_sample() -> dict[str, float | None]:
    """One reading of the vitals the dashboard shows."""
    memory = psutil.virtual_memory()
    battery = getattr(psutil, "sensors_battery", lambda: None)()

    disk_percent: float | None = None
    try:
        # The primary volume is the one users mean by "my disk".
        partitions = psutil.disk_partitions(all=False)
        if partitions:
            disk_percent = psutil.disk_usage(partitions[0].mountpoint).percent
    except (OSError, PermissionError):  # pragma: no cover - empty card readers
        disk_percent = None

    return {
        # interval=None returns usage since the previous call, which is exactly
        # the sampling window -- and unlike interval=0.3 it does not block.
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "disk_percent": disk_percent,
        "battery_percent": round(battery.percent, 1) if battery else None,
    }


class MetricSampler:
    """Records a sample on an interval until stopped."""

    def __init__(
        self,
        store: Any,
        interval: float = SAMPLE_INTERVAL_SECONDS,
        retention_hours: int = RETENTION_HOURS,
    ) -> None:
        self._store = store
        self._interval = interval
        self._retention_hours = retention_hours
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        # Prime the CPU counter so the first recorded sample is a real reading
        # rather than the meaningless "since process start" figure.
        psutil.cpu_percent(interval=None)
        pruned_at = 0.0
        while True:
            try:
                await asyncio.sleep(self._interval)
                self._store.record_metrics(take_sample())

                now = asyncio.get_running_loop().time()
                if now - pruned_at > 3600:
                    removed = self._store.prune_metrics(self._retention_hours)
                    pruned_at = now
                    if removed:
                        logger.info("pruned %d old metric samples", removed)
            except asyncio.CancelledError:
                raise
            except Exception:  # a sampling failure must not kill the loop
                logger.exception("metric sampling failed")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info("metric sampler started (%.0fs interval)", self._interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
