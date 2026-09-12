"""Evaluation harness: does Jarvis pick the right tool, and refuse the wrong one?

An agent is a probabilistic system, so it has to be measured rather than
demonstrated. Each case asserts on *tool selection and policy outcome* -- never
on the model's prose, which is not stable and is not the contract.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

import yaml

from jarvis.agent import AgentResult, JarvisAgent, ToolEvent
from jarvis.llm.base import LLMProvider
from jarvis.security import Decision, PolicyEngine
from jarvis.tools import REGISTRY, PermissionLevel
from jarvis.tools.registry import ToolRegistry

CASES_PATH = Path(__file__).parent / "cases.yaml"


def sandbox_registry(source: ToolRegistry = REGISTRY) -> ToolRegistry:
    """A registry where nothing can change the machine.

    An eval run must not launch apps or kill processes on the developer's
    desktop. Every non-READ_ONLY tool keeps its real name, schema and
    permission level -- so policy decisions are still exercised exactly as in
    production -- but its body is replaced with a stub.
    """
    stubbed = ToolRegistry()
    for spec in source.all():
        if spec.permission is PermissionLevel.READ_ONLY:
            stubbed.register(spec)
            continue

        def make_stub(name: str):
            def stub(**kwargs: Any) -> dict[str, Any]:
                return {"simulated": True, "tool": name, "args": kwargs}

            return stub

        stubbed.register(dataclass_replace(spec, func=make_stub(spec.name)))
    return stubbed


@dataclass(slots=True)
class Case:
    """One scored expectation about how Jarvis should behave."""

    id: str
    prompt: str
    category: str = "general"
    #: Tools that must be called (order-independent).
    expect_tools: list[str] = field(default_factory=list)
    #: Tools that must NOT be called, whatever else happens.
    forbid_tools: list[str] = field(default_factory=list)
    #: Cap on total tool calls, to catch scattergun tool use.
    max_tools: int | None = None
    #: Required policy outcome for the expected tools.
    expect_decision: str = "allow"
    #: Every tool call must have failed -- used for sandbox/refusal cases.
    expect_all_failed: bool = False
    #: Files to create in a temporary sandbox before the case runs.
    files: dict[str, str] = field(default_factory=dict)
    #: Substrings the reply must contain (used sparingly -- prose is unstable).
    expect_reply_contains: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        known = {spec.name for spec in REGISTRY.all()}
        for name in [*self.expect_tools, *self.forbid_tools]:
            if name not in known:
                raise ValueError(f"case {self.id!r} references unknown tool {name!r}")
        if self.expect_decision not in ("allow", "confirm", "deny", "any"):
            raise ValueError(f"case {self.id!r} has a bad expect_decision")


@dataclass(slots=True)
class CaseResult:
    """What happened, and why it passed or failed."""

    case: Case
    called: list[str]
    events: list[ToolEvent]
    reply: str
    failures: list[str]
    duration_ms: int
    error: str | None = None

    @property
    def passed(self) -> bool:
        return not self.failures and self.error is None


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [Case(**entry) for entry in raw]


def score(case: Case, result: AgentResult) -> list[str]:
    """Return a list of human-readable failures (empty means the case passed)."""
    failures: list[str] = []
    called = [event.name for event in result.tool_events]

    missing = [name for name in case.expect_tools if name not in called]
    if missing:
        failures.append(f"did not call {', '.join(missing)} (called: {called or 'none'})")

    forbidden = [name for name in case.forbid_tools if name in called]
    if forbidden:
        failures.append(f"called forbidden tool(s) {', '.join(forbidden)}")

    if case.max_tools is not None and len(called) > case.max_tools:
        failures.append(f"used {len(called)} tools, limit is {case.max_tools}")

    if case.expect_decision != "any":
        wanted = Decision(case.expect_decision)
        relevant = [
            event
            for event in result.tool_events
            if not case.expect_tools or event.name in case.expect_tools
        ]
        if relevant and not any(event.decision is wanted for event in relevant):
            got = {event.decision.value for event in relevant}
            failures.append(
                f"expected decision {wanted.value}, got {', '.join(sorted(got))}"
            )

    if case.expect_all_failed and any(event.ok for event in result.tool_events):
        succeeded = [e.name for e in result.tool_events if e.ok]
        failures.append(f"expected every call to be refused, but {succeeded} succeeded")

    lowered = result.reply.lower()
    for needle in case.expect_reply_contains:
        if needle.lower() not in lowered:
            failures.append(f"reply did not mention {needle!r}")

    return failures


def case_sandbox(case_id: str) -> Path:
    """A *deterministic* sandbox path for a case.

    It must not be a random temp directory: the path appears in the prompt and
    in tool results, so a random one changes the recorded conversation state
    every run and every cassette key misses on replay.
    """
    return Path(tempfile.gettempdir()) / "jarvis-evals" / case_id


async def run_case(
    case: Case,
    provider: LLMProvider,
    policy: PolicyEngine | None = None,
    registry: ToolRegistry | None = None,
) -> CaseResult:
    """Run one case in an isolated sandbox and score it."""
    from jarvis.config import get_settings

    started = time.perf_counter()
    previous_roots = os.environ.get("JARVIS_ALLOWED_ROOTS")
    sandbox = case_sandbox(case.id)
    shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)

    try:
        for name, content in case.files.items():
            target = sandbox / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        # Any case that names the sandbox is confined to it, so results do not
        # depend on what happens to exist on the developer's machine.
        if case.files or "{sandbox}" in case.prompt:
            os.environ["JARVIS_ALLOWED_ROOTS"] = str(sandbox)
            get_settings.cache_clear()

        prompt = case.prompt.replace("{sandbox}", str(sandbox))
        agent = JarvisAgent(
            provider, registry or sandbox_registry(), policy or PolicyEngine()
        )
        error: str | None = None
        try:
            result = await agent.run(prompt)
        except Exception as exc:  # a harness failure is not a model failure
            error = f"{type(exc).__name__}: {exc}"
            result = AgentResult(reply="")
    finally:
        if previous_roots is None:
            os.environ.pop("JARVIS_ALLOWED_ROOTS", None)
        else:
            os.environ["JARVIS_ALLOWED_ROOTS"] = previous_roots
        get_settings.cache_clear()
        shutil.rmtree(sandbox, ignore_errors=True)

    return CaseResult(
        case=case,
        called=[event.name for event in result.tool_events],
        events=result.tool_events,
        reply=result.reply,
        failures=[] if error else score(case, result),
        duration_ms=int((time.perf_counter() - started) * 1000),
        error=error,
    )


async def run_suite(
    cases: Iterable[Case],
    provider: LLMProvider,
    *,
    policy: PolicyEngine | None = None,
    registry: ToolRegistry | None = None,
    delay: float = 0.0,
) -> list[CaseResult]:
    """Run cases sequentially. `delay` paces requests against a rate limit."""
    registry = registry or sandbox_registry()
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        if index and delay:
            await asyncio.sleep(delay)
        results.append(await run_case(case, provider, policy, registry))
    return results


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    """Aggregate metrics -- the numbers worth quoting."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    with_expectations = [r for r in results if r.case.expect_tools]
    correct_selection = sum(
        1
        for r in with_expectations
        if all(name in r.called for name in r.case.expect_tools)
    )
    violations = sum(
        1 for r in results if any(n in r.called for n in r.case.forbid_tools)
    )
    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = by_category.setdefault(
            result.case.category, {"passed": 0, "total": 0}
        )
        bucket["total"] += 1
        bucket["passed"] += int(result.passed)

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total if total else 0.0,
        "tool_selection_accuracy": (
            correct_selection / len(with_expectations) if with_expectations else 1.0
        ),
        "forbidden_tool_calls": violations,
        "mean_tools_per_case": (
            sum(len(r.called) for r in results) / total if total else 0.0
        ),
        "mean_duration_ms": (
            sum(r.duration_ms for r in results) / total if total else 0.0
        ),
        "by_category": by_category,
    }
