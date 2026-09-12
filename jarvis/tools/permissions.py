"""Permission levels that gate every tool call."""

from __future__ import annotations

from enum import Enum


class PermissionLevel(str, Enum):
    """How much trust a tool needs before it may run.

    Ordering matters: higher value == more dangerous.
    """

    READ_ONLY = "READ_ONLY"
    LOW_RISK = "LOW_RISK"
    CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
    BLOCKED = "BLOCKED"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def requires_confirmation(self) -> bool:
        return self is PermissionLevel.CONFIRM_REQUIRED

    def is_blocked(self) -> bool:
        return self is PermissionLevel.BLOCKED


_RANK: dict[PermissionLevel, int] = {
    PermissionLevel.READ_ONLY: 0,
    PermissionLevel.LOW_RISK: 1,
    PermissionLevel.CONFIRM_REQUIRED: 2,
    PermissionLevel.BLOCKED: 3,
}
