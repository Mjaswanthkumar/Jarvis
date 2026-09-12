from __future__ import annotations

import pytest

from jarvis.tools import shell


def test_only_allowlisted_executables_can_be_resolved() -> None:
    with pytest.raises(shell.CommandNotAllowed):
        shell.find_executable("powershell")


def test_missing_executable_reports_clearly() -> None:
    shell.find_executable.cache_clear()
    with pytest.raises((shell.ExecutableNotFound, shell.CommandNotAllowed)):
        shell.find_executable("npx-that-does-not-exist")


def test_run_rejects_non_allowlisted_command() -> None:
    with pytest.raises(shell.CommandNotAllowed):
        shell.run(["cmd", "/c", "echo hi"])


def test_run_rejects_empty_argv() -> None:
    with pytest.raises(ValueError):
        shell.run([])


def test_arguments_are_never_shell_interpreted() -> None:
    """A metacharacter must arrive as one literal argument, not a second command."""
    result = shell.run(
        ["python", "-c", "import sys; print(sys.argv[1])", "& echo pwned"],
        timeout=30,
    )
    assert result.ok
    assert result.stdout == "& echo pwned"


def test_successful_command_reports_output() -> None:
    result = shell.run(["git", "--version"])
    assert result.ok
    assert result.stdout.startswith("git version")


def test_failed_command_raises_with_context() -> None:
    result = shell.run(["git", "rev-parse", "--verify", "no-such-ref-xyz"])
    assert not result.ok
    with pytest.raises(RuntimeError, match="failed"):
        result.raise_for_status()


def test_timeout_is_reported_not_raised() -> None:
    result = shell.run(["python", "-c", "import time; time.sleep(5)"], timeout=0.5)
    assert result.timed_out
    assert not result.ok
    with pytest.raises(TimeoutError):
        result.raise_for_status()


def test_long_output_is_truncated() -> None:
    result = shell.run(["python", "-c", "print('x' * 50000)"], timeout=30)
    assert result.ok
    assert "output truncated" in result.stdout
    assert len(result.stdout) < 50000
