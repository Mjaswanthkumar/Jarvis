"""Tests for the eval harness itself -- scoring bugs would hide agent bugs."""

from __future__ import annotations

import pytest

from evals.harness import Case, CaseResult, load_cases, sandbox_registry, score, summarize
from jarvis.agent import AgentResult, ToolEvent
from jarvis.security import Decision
from jarvis.tools import REGISTRY
from jarvis.tools.permissions import PermissionLevel


def _event(
    name: str,
    ok: bool = True,
    decision: Decision = Decision.ALLOW,
    permission: PermissionLevel = PermissionLevel.READ_ONLY,
) -> ToolEvent:
    return ToolEvent(
        name=name, ok=ok, permission=permission, decision=decision, args={}
    )


def _result(*events: ToolEvent, reply: str = "done") -> AgentResult:
    return AgentResult(reply=reply, tool_events=list(events))


def test_shipped_cases_all_load_and_reference_real_tools() -> None:
    cases = load_cases()
    assert len(cases) >= 30
    assert {c.category for c in cases} >= {"system", "safety", "injection"}
    assert len({c.id for c in cases}) == len(cases), "duplicate case ids"


def test_case_rejects_an_unknown_tool_name() -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        Case(id="x", prompt="p", expect_tools=["not_a_tool"])


def test_case_rejects_a_bad_decision() -> None:
    with pytest.raises(ValueError, match="expect_decision"):
        Case(id="x", prompt="p", expect_decision="maybe")


def test_expected_tool_called_passes() -> None:
    case = Case(id="x", prompt="p", expect_tools=["cpu_info"])
    assert score(case, _result(_event("cpu_info"))) == []


def test_missing_expected_tool_fails() -> None:
    case = Case(id="x", prompt="p", expect_tools=["cpu_info"])
    failures = score(case, _result())
    assert failures and "did not call cpu_info" in failures[0]


def test_forbidden_tool_fails() -> None:
    case = Case(id="x", prompt="p", forbid_tools=["close_application"])
    failures = score(case, _result(_event("close_application")))
    assert any("forbidden" in f for f in failures)


def test_max_tools_is_enforced() -> None:
    case = Case(id="x", prompt="p", max_tools=1)
    failures = score(case, _result(_event("cpu_info"), _event("memory_info")))
    assert any("limit is 1" in f for f in failures)


def test_zero_max_tools_catches_unnecessary_calls() -> None:
    case = Case(id="x", prompt="hi", max_tools=0)
    assert score(case, _result()) == []
    assert score(case, _result(_event("cpu_info"))) != []


def test_expected_decision_is_checked() -> None:
    case = Case(id="x", prompt="p", expect_tools=["close_application"],
                expect_decision="confirm")
    confirmed = _event("close_application", ok=False, decision=Decision.CONFIRM)
    assert score(case, _result(confirmed)) == []

    allowed = _event("close_application", decision=Decision.ALLOW)
    assert score(case, _result(allowed)) != []


def test_any_decision_skips_the_check() -> None:
    case = Case(id="x", prompt="p", expect_decision="any")
    assert score(case, _result(_event("cpu_info", decision=Decision.DENY))) == []


def test_expect_all_failed_catches_a_successful_call() -> None:
    case = Case(id="x", prompt="p", expect_all_failed=True)
    assert score(case, _result(_event("cpu_info", ok=False))) == []
    assert score(case, _result(_event("cpu_info", ok=True))) != []


def test_reply_substring_check() -> None:
    case = Case(id="x", prompt="p", expect_reply_contains=["denied"])
    assert score(case, _result(reply="Access was DENIED")) == []
    assert score(case, _result(reply="all good")) != []


def test_summary_metrics() -> None:
    passing = CaseResult(
        case=Case(id="a", prompt="p", category="system", expect_tools=["cpu_info"]),
        called=["cpu_info"],
        events=[],
        reply="",
        failures=[],
        duration_ms=100,
    )
    failing = CaseResult(
        case=Case(id="b", prompt="p", category="safety",
                  forbid_tools=["close_application"]),
        called=["close_application"],
        events=[],
        reply="",
        failures=["called forbidden tool(s) close_application"],
        duration_ms=200,
    )
    metrics = summarize([passing, failing])

    assert metrics["total"] == 2
    assert metrics["passed"] == 1
    assert metrics["pass_rate"] == 0.5
    assert metrics["tool_selection_accuracy"] == 1.0  # only 'a' had expectations
    assert metrics["forbidden_tool_calls"] == 1
    assert metrics["by_category"]["safety"] == {"passed": 0, "total": 1}


# ------------------------------------------------------- sandbox registry ----
def test_sandbox_registry_preserves_names_and_permissions() -> None:
    stubbed = sandbox_registry()
    assert {s.name for s in stubbed.available()} == {
        s.name for s in REGISTRY.available()
    }
    for spec in REGISTRY.available():
        assert stubbed.get(spec.name).permission is spec.permission


def test_sandbox_registry_stubs_state_changing_tools() -> None:
    """An eval run must not launch apps on the developer's desktop."""
    result = sandbox_registry().execute("open_application", {"name": "notepad"})
    assert result.ok
    assert result.data == {
        "simulated": True,
        "tool": "open_application",
        "args": {"name": "notepad"},
    }


def test_sandbox_registry_keeps_read_only_tools_real() -> None:
    result = sandbox_registry().execute("memory_info")
    assert result.ok
    assert result.data["total_gb"] > 0


def test_sandbox_registry_still_validates_arguments() -> None:
    result = sandbox_registry().execute("open_application", {"wrong": 1})
    assert not result.ok
    assert "invalid arguments" in (result.error or "")
