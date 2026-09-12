"""``python -m evals`` -- run the suite and print a report.

    python -m evals                 replay a cassette (fast, offline, CI)
    python -m evals --live          hit the real model
    python -m evals --live --record hit it and save a new cassette
    python -m evals --filter safety run one category or case id
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from evals.harness import CaseResult, load_cases, run_suite, summarize
from evals.recorder import RecordingProvider, ReplayProvider
from jarvis.llm.factory import get_provider

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def report(results: list[CaseResult], threshold: float) -> bool:
    metrics = summarize(results)

    print(f"\n{BOLD}Case results{RESET}")
    for result in results:
        mark = f"{GREEN}PASS{RESET}" if result.passed else f"{RED}FAIL{RESET}"
        tools = ", ".join(result.called) or "-"
        print(f"  {mark}  {result.case.id:34} {DIM}{tools}{RESET}")
        for failure in result.failures:
            print(f"        {RED}-> {failure}{RESET}")
        if result.error:
            print(f"        {RED}-> harness error: {result.error}{RESET}")

    print(f"\n{BOLD}By category{RESET}")
    for name, bucket in sorted(metrics["by_category"].items()):
        rate = bucket["passed"] / bucket["total"]
        colour = GREEN if rate == 1.0 else RED
        print(
            f"  {name:14} {colour}{bucket['passed']:>2}/{bucket['total']:<2}{RESET}"
            f" {DIM}{rate:.0%}{RESET}"
        )

    print(f"\n{BOLD}Metrics{RESET}")
    print(f"  pass rate                {metrics['pass_rate']:.1%}"
          f" ({metrics['passed']}/{metrics['total']})")
    print(f"  tool-selection accuracy  {metrics['tool_selection_accuracy']:.1%}")
    print(f"  forbidden tool calls     {metrics['forbidden_tool_calls']}")
    print(f"  mean tools per case      {metrics['mean_tools_per_case']:.2f}")
    print(f"  mean duration            {metrics['mean_duration_ms']:.0f} ms")

    ok = metrics["pass_rate"] >= threshold and metrics["forbidden_tool_calls"] == 0
    verdict = f"{GREEN}PASSED{RESET}" if ok else f"{RED}FAILED{RESET}"
    print(f"\n  {verdict} (threshold {threshold:.0%}, zero forbidden calls)\n")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser(prog="evals")
    parser.add_argument("--live", action="store_true", help="use the real model")
    parser.add_argument("--record", action="store_true", help="save a cassette")
    parser.add_argument("--filter", default="", help="match a case id or category")
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument(
        "--delay",
        type=float,
        default=4.5,
        help="seconds between live cases, to respect the rate limit",
    )
    args = parser.parse_args()

    cases = load_cases()
    if args.filter:
        needle = args.filter.lower()
        cases = [c for c in cases if needle in c.id.lower() or needle == c.category]
    if not cases:
        print("no cases matched")
        return 1

    if args.live or args.record:
        provider = RecordingProvider(get_provider()) if args.record else get_provider()
        if not provider.is_configured():
            print("GEMINI_API_KEY is not set")
            return 1
        delay = args.delay
    else:
        provider = ReplayProvider()
        delay = 0.0

    mode = "live" if (args.live or args.record) else "replay"
    print(f"{BOLD}Running {len(cases)} cases ({mode}){RESET}")

    results = await run_suite(cases, provider, delay=delay)

    if isinstance(provider, RecordingProvider):
        print(f"  cassette saved: {provider.save()} exchanges")

    return 0 if report(results, args.threshold) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
