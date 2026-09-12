from __future__ import annotations

import pytest

from jarvis.security import Decision, PolicyEngine, summarize_call
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import ToolRegistry, ToolSpec, tool


def _spec(name: str, level: PermissionLevel) -> ToolSpec:
    registry = ToolRegistry()

    @tool(name=name, description="x" * 30, permission=level, registry=registry)
    def _fn() -> None:  # pragma: no cover - never executed here
        return None

    spec = registry.get(name)
    assert spec is not None
    return spec


READ_ONLY = _spec("reader", PermissionLevel.READ_ONLY)
LOW_RISK = _spec("opener", PermissionLevel.LOW_RISK)
CONFIRM = _spec("closer", PermissionLevel.CONFIRM_REQUIRED)
BLOCKED = _spec("nuke", PermissionLevel.BLOCKED)


def test_read_only_and_low_risk_are_allowed() -> None:
    policy = PolicyEngine()
    assert policy.evaluate(READ_ONLY, tool_name="reader").decision is Decision.ALLOW
    assert policy.evaluate(LOW_RISK, tool_name="opener").decision is Decision.ALLOW


def test_confirm_required_needs_approval() -> None:
    policy = PolicyEngine()
    verdict = policy.evaluate(CONFIRM, tool_name="closer")
    assert verdict.decision is Decision.CONFIRM
    assert "confirmation" in verdict.reason


def test_approval_unlocks_a_confirm_required_tool() -> None:
    verdict = PolicyEngine().evaluate(CONFIRM, tool_name="closer", approved=True)
    assert verdict.decision is Decision.ALLOW


def test_blocked_permission_is_always_denied() -> None:
    verdict = PolicyEngine().evaluate(BLOCKED, tool_name="nuke", approved=True)
    assert verdict.decision is Decision.DENY


def test_unknown_tool_is_denied() -> None:
    verdict = PolicyEngine().evaluate(None, tool_name="ghost")
    assert verdict.decision is Decision.DENY
    assert "unknown tool" in verdict.reason


def test_configured_blocklist_wins_over_permission_level() -> None:
    policy = PolicyEngine(blocked_tools={"reader"})
    verdict = policy.evaluate(READ_ONLY, tool_name="reader")
    assert verdict.decision is Decision.DENY
    assert "disabled" in verdict.reason


def test_auto_approve_skips_confirmation() -> None:
    policy = PolicyEngine(auto_approve={"closer"})
    assert policy.evaluate(CONFIRM, tool_name="closer").decision is Decision.ALLOW


def test_read_only_mode_denies_everything_that_changes_state() -> None:
    policy = PolicyEngine(read_only_mode=True)
    assert policy.evaluate(READ_ONLY, tool_name="reader").decision is Decision.ALLOW
    assert policy.evaluate(LOW_RISK, tool_name="opener").decision is Decision.DENY
    assert (
        policy.evaluate(CONFIRM, tool_name="closer", approved=True).decision
        is Decision.DENY
    )


def test_read_only_mode_cannot_be_overridden_by_auto_approve() -> None:
    policy = PolicyEngine(auto_approve={"closer"}, read_only_mode=True)
    assert policy.evaluate(CONFIRM, tool_name="closer").decision is Decision.DENY


def test_settings_drive_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.config import get_settings

    monkeypatch.setenv("JARVIS_BLOCKED_TOOLS", "close_application, run_tests")
    monkeypatch.setenv("JARVIS_AUTO_APPROVE_TOOLS", "open_path")
    monkeypatch.setenv("JARVIS_READ_ONLY_MODE", "true")
    get_settings.cache_clear()
    try:
        policy = PolicyEngine.from_settings()
        assert policy.blocked_tools == {"close_application", "run_tests"}
        assert policy.auto_approve == {"open_path"}
        assert policy.read_only_mode is True
    finally:
        get_settings.cache_clear()


def test_summaries_describe_the_action() -> None:
    assert summarize_call("close_application", {"name": "spotify"}) == (
        "Run close_application with name='spotify'"
    )
    assert summarize_call("cpu_info", {}) == "Run cpu_info"
