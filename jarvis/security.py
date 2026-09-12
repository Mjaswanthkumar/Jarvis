"""The policy layer that sits between tool selection and tool execution.

The registry states what a tool *is* (its permission level); this module decides
whether that tool may run *right now*, given the user's configuration and what
they have explicitly approved. The agent never bypasses it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from jarvis.config import Settings, get_settings
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import ToolSpec

logger = logging.getLogger(__name__)


class Decision(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(slots=True, frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str
    permission: PermissionLevel

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


def _split(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


class PolicyEngine:
    """Evaluates a requested tool call against the configured policy.

    Configuration can only make Jarvis *more* restrictive, with one deliberate
    exception: ``auto_approve`` pre-approves named CONFIRM_REQUIRED tools, which
    is how an unattended or scripted setup opts in ahead of time.
    """

    def __init__(
        self,
        *,
        blocked_tools: set[str] | None = None,
        auto_approve: set[str] | None = None,
        read_only_mode: bool = False,
    ) -> None:
        self.blocked_tools = blocked_tools or set()
        self.auto_approve = auto_approve or set()
        self.read_only_mode = read_only_mode

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> PolicyEngine:
        settings = settings or get_settings()
        return cls(
            blocked_tools=_split(settings.jarvis_blocked_tools),
            auto_approve=_split(settings.jarvis_auto_approve_tools),
            read_only_mode=settings.jarvis_read_only_mode,
        )

    def evaluate(
        self,
        spec: ToolSpec | None,
        *,
        tool_name: str,
        approved: bool = False,
    ) -> PolicyDecision:
        if spec is None:
            return PolicyDecision(
                Decision.DENY, f"unknown tool: {tool_name}", PermissionLevel.BLOCKED
            )

        level = spec.permission

        if level.is_blocked():
            return PolicyDecision(
                Decision.DENY, f"'{spec.name}' is blocked by policy", level
            )
        if spec.name in self.blocked_tools:
            return PolicyDecision(
                Decision.DENY,
                f"'{spec.name}' is disabled in this Jarvis configuration",
                level,
            )
        if self.read_only_mode and level is not PermissionLevel.READ_ONLY:
            return PolicyDecision(
                Decision.DENY,
                f"'{spec.name}' changes machine state and Jarvis is in read-only mode",
                level,
            )
        if level.requires_confirmation():
            if approved:
                return PolicyDecision(
                    Decision.ALLOW, "approved by the user", level
                )
            if spec.name in self.auto_approve:
                return PolicyDecision(
                    Decision.ALLOW, "pre-approved by configuration", level
                )
            return PolicyDecision(
                Decision.CONFIRM,
                f"'{spec.name}' needs your confirmation before it runs",
                level,
            )
        return PolicyDecision(Decision.ALLOW, f"{level.value} tool", level)


def summarize_call(tool_name: str, args: dict[str, Any]) -> str:
    """A one-line, human-readable description of a pending action."""
    if not args:
        return f"Run {tool_name}"
    rendered = ", ".join(f"{key}={value!r}" for key, value in args.items())
    return f"Run {tool_name} with {rendered}"
