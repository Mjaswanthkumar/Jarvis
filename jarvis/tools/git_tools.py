"""Git inspection tools plus a guarded test runner."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from jarvis.tools import shell
from jarvis.tools.paths import default_root, resolve_in_sandbox
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import tool

_GIT_TIMEOUT = 20.0
_TEST_TIMEOUT = 600.0


def _repo(path: str | None) -> Path:
    """Resolve a sandboxed path and confirm it is inside a git work tree."""
    target = resolve_in_sandbox(path) if path else default_root()
    if target.is_file():
        target = target.parent
    result = shell.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=target, timeout=_GIT_TIMEOUT
    )
    if not result.ok:
        raise RuntimeError(f"'{target}' is not inside a git repository")
    # git walks upwards, so re-check the discovered work tree against the sandbox.
    return resolve_in_sandbox(result.stdout.strip())


@tool(
    description=(
        "Git status for a repository: current branch, upstream tracking, "
        "ahead/behind counts, staged and unstaged changes, untracked files."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("git",),
    untrusted_output=True,
)
def git_status(repo_path: str | None = None) -> dict[str, Any]:
    repo = _repo(repo_path)
    porcelain = shell.run(
        ["git", "status", "--porcelain=v2", "--branch"],
        cwd=repo,
        timeout=_GIT_TIMEOUT,
    ).raise_for_status()

    branch = "(detached)"
    upstream: str | None = None
    ahead = behind = 0
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    conflicted: list[str] = []

    for line in porcelain.stdout.splitlines():
        if line.startswith("# branch.head"):
            branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.upstream"):
            upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab"):
            parts = line.split()
            ahead, behind = int(parts[2]), abs(int(parts[3]))
        elif line.startswith(("1 ", "2 ")):
            fields = line.split(" ", 8)
            xy, path = fields[1], fields[-1]
            if xy[0] != ".":
                staged.append(path)
            if xy[1] != ".":
                unstaged.append(path)
        elif line.startswith("? "):
            untracked.append(line[2:])
        elif line.startswith("u "):
            conflicted.append(line.split(" ", 10)[-1])

    last = shell.run(
        ["git", "log", "-1", "--pretty=%h|%an|%ar|%s"],
        cwd=repo,
        timeout=_GIT_TIMEOUT,
    )
    last_commit: dict[str, str] | None = None
    if last.ok and last.stdout:
        sha, author, when, subject = last.stdout.split("|", 3)
        last_commit = {
            "sha": sha,
            "author": author,
            "when": when,
            "subject": subject,
        }

    return {
        "repo": str(repo),
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "clean": not (staged or unstaged or untracked or conflicted),
        "staged": staged[:50],
        "unstaged": unstaged[:50],
        "untracked": untracked[:50],
        "conflicted": conflicted[:50],
        "last_commit": last_commit,
    }


@tool(
    description="Recent commits in a repository: sha, author, relative date, subject.",
    permission=PermissionLevel.READ_ONLY,
    tags=("git",),
    untrusted_output=True,
)
def git_log(repo_path: str | None = None, limit: int = 10) -> dict[str, Any]:
    repo = _repo(repo_path)
    limit = max(1, min(limit, 50))
    result = shell.run(
        ["git", "log", f"-{limit}", "--pretty=%h|%an|%ar|%s"],
        cwd=repo,
        timeout=_GIT_TIMEOUT,
    ).raise_for_status()

    commits = []
    for line in result.stdout.splitlines():
        sha, author, when, subject = line.split("|", 3)
        commits.append(
            {"sha": sha, "author": author, "when": when, "subject": subject}
        )
    return {"repo": str(repo), "commits": commits}


@tool(
    description=(
        "List local branches with their last commit, marking the current one."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("git",),
    untrusted_output=True,
)
def git_branches(repo_path: str | None = None, limit: int = 30) -> dict[str, Any]:
    repo = _repo(repo_path)
    limit = max(1, min(limit, 100))
    result = shell.run(
        [
            "git",
            "for-each-ref",
            "--sort=-committerdate",
            f"--count={limit}",
            "--format=%(refname:short)|%(HEAD)|%(committerdate:relative)|%(subject)",
            "refs/heads",
        ],
        cwd=repo,
        timeout=_GIT_TIMEOUT,
    ).raise_for_status()

    branches = []
    for line in result.stdout.splitlines():
        name, head, when, subject = line.split("|", 3)
        branches.append(
            {
                "name": name,
                "current": head.strip() == "*",
                "last_commit": when,
                "subject": subject,
            }
        )
    return {"repo": str(repo), "branches": branches}


@tool(
    description=(
        "Summarise uncommitted changes: files touched with insertion/deletion "
        "counts. Use when asked 'what have I changed?'."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("git",),
    untrusted_output=True,
)
def git_diff_stat(repo_path: str | None = None, staged: bool = False) -> dict[str, Any]:
    repo = _repo(repo_path)
    argv = ["git", "diff", "--numstat"]
    if staged:
        argv.append("--cached")
    result = shell.run(argv, cwd=repo, timeout=_GIT_TIMEOUT).raise_for_status()

    files = []
    total_added = total_removed = 0
    for line in result.stdout.splitlines():
        added, removed, path = line.split("\t", 2)
        added_n = 0 if added == "-" else int(added)
        removed_n = 0 if removed == "-" else int(removed)
        total_added += added_n
        total_removed += removed_n
        files.append({"path": path, "added": added_n, "removed": removed_n})

    return {
        "repo": str(repo),
        "staged": staged,
        "files": files[:50],
        "files_changed": len(files),
        "insertions": total_added,
        "deletions": total_removed,
    }


@tool(
    description=(
        "Run a project's test suite (pytest or npm test, auto-detected) and "
        "report pass/fail counts. Executes project code, so it needs "
        "confirmation."
    ),
    permission=PermissionLevel.CONFIRM_REQUIRED,
    tags=("git", "dev"),
    untrusted_output=True,
)
def run_tests(project_path: str | None = None, framework: str = "auto") -> dict[str, Any]:
    project = resolve_in_sandbox(project_path) if project_path else default_root()
    if project.is_file():
        project = project.parent
    if framework not in ("auto", "pytest", "npm"):
        raise ValueError("framework must be 'auto', 'pytest' or 'npm'")

    if framework == "auto":
        framework = _detect_framework(project)

    if framework == "pytest":
        argv = [str(_project_python(project)), "-m", "pytest", "-q", "--no-header"]
    else:
        argv = ["npm", "test", "--silent"]

    result = shell.run(argv, cwd=project, timeout=_TEST_TIMEOUT)
    tail = "\n".join((result.stdout or result.stderr).splitlines()[-25:])
    return {
        "project": str(project),
        "framework": framework,
        "passed": result.ok,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "summary": _summarise_pytest(result.stdout) if framework == "pytest" else None,
        "output_tail": tail,
    }


def _project_python(project: Path) -> Path:
    """Use the project's own virtualenv interpreter when it has one."""
    relative = (
        Path(".venv/Scripts/python.exe")
        if sys.platform == "win32"
        else Path(".venv/bin/python")
    )
    venv_python = project / relative
    return venv_python if venv_python.is_file() else Path(sys.executable)


def _detect_framework(project: Path) -> str:
    if any(
        (project / marker).exists()
        for marker in ("pytest.ini", "pyproject.toml", "tox.ini", "tests", "setup.cfg")
    ):
        return "pytest"
    if (project / "package.json").exists():
        return "npm"
    raise FileNotFoundError(f"no recognised test setup found in {project}")


def _summarise_pytest(output: str) -> str | None:
    """Pull pytest's own one-line summary out of the tail of its output."""
    for line in reversed(output.splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            return line.strip("= ")
    return None
