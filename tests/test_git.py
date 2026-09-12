from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis.config import get_settings
from jarvis.tools import REGISTRY
from jarvis.tools.permissions import PermissionLevel


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway repository with one commit and assorted dirty state."""
    monkeypatch.setenv("JARVIS_ALLOWED_ROOTS", str(tmp_path))
    get_settings.cache_clear()

    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    _git(repo_path, "init", "-q", "-b", "main")
    _git(repo_path, "config", "user.email", "test@example.com")
    _git(repo_path, "config", "user.name", "Test")

    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo_path, "add", "README.md")
    _git(repo_path, "commit", "-qm", "initial commit")

    (repo_path / "README.md").write_text("hello\nworld\n", encoding="utf-8")
    (repo_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repo_path, "add", "staged.txt")
    (repo_path / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    yield repo_path
    get_settings.cache_clear()


def test_git_status_classifies_changes(repo: Path) -> None:
    result = REGISTRY.execute("git_status", {"repo_path": str(repo)})
    assert result.ok, result.error
    data = result.data
    assert data["branch"] == "main"
    assert data["clean"] is False
    assert data["staged"] == ["staged.txt"]
    assert data["unstaged"] == ["README.md"]
    assert data["untracked"] == ["untracked.txt"]
    assert data["last_commit"]["subject"] == "initial commit"
    assert data["upstream"] is None


def test_git_status_reports_a_clean_repo(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second")
    result = REGISTRY.execute("git_status", {"repo_path": str(repo)})
    assert result.data["clean"] is True


def test_git_status_rejects_a_non_repository(repo: Path, tmp_path: Path) -> None:
    """A folder with no repo of its own must not report an ancestor's repo.

    git searches upwards, so on a machine where a parent directory happens to be
    a repository the discovered work tree is rejected by the sandbox instead.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    result = REGISTRY.execute("git_status", {"repo_path": str(plain)})
    assert not result.ok
    assert "not inside a git repository" in (
        result.error or ""
    ) or "outside the allowed roots" in (result.error or "")


def test_git_status_rejects_paths_outside_the_sandbox(repo: Path) -> None:
    result = REGISTRY.execute("git_status", {"repo_path": "C:\\Windows"})
    assert not result.ok
    assert "outside the allowed roots" in (result.error or "")


def test_git_log_returns_commits(repo: Path) -> None:
    result = REGISTRY.execute("git_log", {"repo_path": str(repo), "limit": 5})
    assert result.ok, result.error
    commits = result.data["commits"]
    assert commits[0]["subject"] == "initial commit"
    assert commits[0]["author"] == "Test"


def test_git_branches_marks_the_current_branch(repo: Path) -> None:
    _git(repo, "branch", "feature/x")
    result = REGISTRY.execute("git_branches", {"repo_path": str(repo)})
    assert result.ok, result.error
    branches = {b["name"]: b for b in result.data["branches"]}
    assert branches["main"]["current"] is True
    assert branches["feature/x"]["current"] is False


def test_git_diff_stat_counts_lines(repo: Path) -> None:
    result = REGISTRY.execute("git_diff_stat", {"repo_path": str(repo)})
    assert result.ok, result.error
    assert result.data["files_changed"] == 1
    assert result.data["insertions"] == 1
    assert result.data["files"][0]["path"] == "README.md"


def test_git_diff_stat_can_target_the_index(repo: Path) -> None:
    result = REGISTRY.execute(
        "git_diff_stat", {"repo_path": str(repo), "staged": True}
    )
    assert result.data["files"][0]["path"] == "staged.txt"


def test_run_tests_requires_confirmation() -> None:
    spec = REGISTRY.get("run_tests")
    assert spec is not None
    assert spec.permission is PermissionLevel.CONFIRM_REQUIRED


def test_run_tests_detects_pytest_projects(repo: Path) -> None:
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    result = REGISTRY.execute("run_tests", {"project_path": str(repo)})
    assert result.ok, result.error
    assert result.data["framework"] == "pytest"
    assert result.data["passed"] is True
    assert "1 passed" in (result.data["summary"] or "")


def test_run_tests_reports_failures(repo: Path) -> None:
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_bad.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    result = REGISTRY.execute("run_tests", {"project_path": str(repo)})
    assert result.ok, result.error
    assert result.data["passed"] is False
    assert "failed" in (result.data["summary"] or "")


def test_run_tests_without_a_known_framework(repo: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = REGISTRY.execute("run_tests", {"project_path": str(empty)})
    assert not result.ok
    assert "no recognised test setup" in (result.error or "")
