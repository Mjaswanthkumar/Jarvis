"""Watches: the agent noticing something without being asked.

Everything else here is reactive -- the user asks, Jarvis answers. A watch
inverts that: a condition over the vitals already being sampled, evaluated on
every sample, which raises an alert the moment it becomes true.

Deliberately *not* an LLM loop. A watch is a comparison against a number, so it
is a comparison against a number: cheap, predictable, and impossible to get
wrong in an interesting way. The model's job is turning "tell me when my disk
gets low" into that comparison, which it does once, at creation time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

#: Metrics a watch can be defined over -- the ones the sampler records.
WATCHABLE = ("cpu", "memory", "disk", "battery")

#: Once a watch fires it stays quiet for this long, so a metric hovering on the
#: threshold cannot produce an alert every thirty seconds.
COOLDOWN_MINUTES = 30

#: A watch needs this many consecutive breaching samples before firing, so a
#: single spike does not wake anyone.
CONSECUTIVE_SAMPLES = 2


class Comparison(str, Enum):
    ABOVE = "above"
    BELOW = "below"

    def breached(self, value: float, threshold: float) -> bool:
        return value > threshold if self is Comparison.ABOVE else value < threshold


@dataclass(slots=True, frozen=True)
class Watch:
    """A condition the user asked to be told about."""

    id: str
    metric: str
    comparison: Comparison
    threshold: float
    note: str = ""

    @property
    def description(self) -> str:
        return (
            f"{self.metric} goes {self.comparison.value} {self.threshold:g}%"
        )


def validate(metric: str, comparison: str, threshold: float) -> tuple[str, Comparison, float]:
    """Normalise and check a watch definition, or raise ValueError."""
    metric = metric.strip().lower()
    if metric not in WATCHABLE:
        raise ValueError(f"metric must be one of {', '.join(WATCHABLE)}")
    try:
        direction = Comparison(comparison.strip().lower())
    except ValueError:
        raise ValueError("comparison must be 'above' or 'below'") from None
    if not 0 <= threshold <= 100:
        raise ValueError("threshold must be a percentage between 0 and 100")
    return metric, direction, float(threshold)


#: Metric name -> the column the sampler writes.
SAMPLE_KEYS = {
    "cpu": "cpu_percent",
    "memory": "memory_percent",
    "disk": "disk_percent",
    "battery": "battery_percent",
}


def evaluate(
    watches: list[dict[str, Any]], sample: dict[str, float | None]
) -> list[tuple[dict[str, Any], float]]:
    """Return the watches this sample breaches, with the offending value.

    Streak counting and cooldown live in the store; this function is pure so it
    can be reasoned about and tested on its own.
    """
    fired: list[tuple[dict[str, Any], float]] = []
    for watch in watches:
        value = sample.get(SAMPLE_KEYS.get(watch["metric"], ""))
        if value is None:
            continue
        if Comparison(watch["comparison"]).breached(value, watch["threshold"]):
            fired.append((watch, float(value)))
    return fired


def alert_text(watch: dict[str, Any], value: float) -> str:
    """What the user reads. Plain, specific, and never alarming for its own sake."""
    metric = watch["metric"]
    direction = "risen above" if watch["comparison"] == "above" else "dropped below"
    label = {"cpu": "CPU", "memory": "Memory", "disk": "Disk", "battery": "Battery"}[
        metric
    ]
    text = f"{label} has {direction} {watch['threshold']:g}% — currently {value:.0f}%."
    if watch.get("note"):
        text += f" ({watch['note']})"
    return text
