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


def test_git_cannot_escape_the_sandbox_upwards(
    tmp_path, monkeypatch
) -> None:
    """git searches upwards, so a stray .git in a parent could answer for a
    folder that is not a repository at all."""
    from jarvis.config import get_settings

    monkeypatch.setenv("JARVIS_ALLOWED_ROOTS", str(tmp_path))
    get_settings.cache_clear()
    try:
        result = shell.run(["git", "status", "--porcelain"], cwd=tmp_path)
        assert not result.ok
        assert "not a git repository" in result.stderr
    finally:
        get_settings.cache_clear()


def test_git_still_resolves_a_repository_inside_the_sandbox(tmp_path, monkeypatch):
    """The ceiling must not break the normal case: a repo below the root."""
    from jarvis.config import get_settings

    repo = tmp_path / "nested" / "project"
    repo.mkdir(parents=True)
    monkeypatch.setenv("JARVIS_ALLOWED_ROOTS", str(tmp_path))
    get_settings.cache_clear()
    try:
        assert shell.run(["git", "init", "-q"], cwd=repo).exit_code == 0
        sub = repo / "src"
        sub.mkdir()
        found = shell.run(["git", "rev-parse", "--show-toplevel"], cwd=sub)
        assert found.ok
        assert "project" in found.stdout
    finally:
        get_settings.cache_clear()
