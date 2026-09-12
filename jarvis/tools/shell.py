"""The only place Jarvis spawns an external process.

Rules enforced here, not by the caller:

* argv lists only -- never ``shell=True``, so no metacharacter injection
* the executable must be on an explicit allowlist and resolvable on PATH
* every run is time-boxed and its output truncated
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Executables Jarvis may spawn. Nothing else can be launched through here.
ALLOWED_EXECUTABLES = frozenset(
    {
        # developer tooling
        "git",
        "docker",
        "kubectl",
        "python",
        "npm",
        "npx",
        # read-only Windows diagnostics (see jarvis.tools.terminal)
        "ipconfig",
        "hostname",
        "whoami",
        "systeminfo",
        "tasklist",
        "netstat",
        "nslookup",
        "ping",
    }
)

_MAX_OUTPUT_CHARS = 8000
DEFAULT_TIMEOUT = 20.0


class ExecutableNotFound(FileNotFoundError):
    """Raised when a required CLI is not installed on this machine."""


class CommandNotAllowed(PermissionError):
    """Raised when a caller tries to spawn something off the allowlist."""


@dataclass(slots=True)
class CommandResult:
    """Outcome of one external command."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
        }

    def raise_for_status(self) -> CommandResult:
        if self.timed_out:
            raise TimeoutError(f"`{self.command}` timed out")
        if self.exit_code != 0:
            detail = (self.stderr or self.stdout or "").strip().splitlines()
            raise RuntimeError(
                f"`{self.command}` failed (exit {self.exit_code}): "
                f"{detail[0] if detail else 'no output'}"
            )
        return self


@lru_cache(maxsize=32)
def find_executable(name: str) -> str:
    """Resolve an allowlisted executable, raising if it is not installed.

    An absolute path is accepted only when its file name is itself on the
    allowlist -- that is how a project's own virtualenv interpreter is used.
    """
    candidate = Path(name)
    if candidate.is_absolute():
        if candidate.stem.lower() not in ALLOWED_EXECUTABLES:
            raise CommandNotAllowed(f"'{name}' is not an allowed executable")
        if not candidate.is_file():
            raise ExecutableNotFound(f"'{name}' does not exist")
        return str(candidate)

    if name not in ALLOWED_EXECUTABLES:
        raise CommandNotAllowed(f"'{name}' is not an allowed executable")
    resolved = shutil.which(name)
    if resolved is None:
        raise ExecutableNotFound(f"'{name}' is not installed or not on PATH")
    return resolved


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> CommandResult:
    """Run an allowlisted command and capture its output."""
    if not argv:
        raise ValueError("argv must not be empty")
    executable = find_executable(argv[0])
    printable = " ".join(argv)

    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False
            [executable, *argv[1:]],
            cwd=str(cwd) if cwd else None,
            env=_environment(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=printable,
            exit_code=-1,
            stdout=_truncate(exc.stdout or ""),
            stderr=_truncate(exc.stderr or ""),
            timed_out=True,
        )

    return CommandResult(
        command=printable,
        exit_code=completed.returncode,
        stdout=_truncate(completed.stdout or ""),
        stderr=_truncate(completed.stderr or ""),
    )


def _environment(cwd: Path | None) -> dict[str, str] | None:
    """Environment for a spawned command, with git's upward search fenced in.

    git looks for a repository by walking *up* from the working directory, so a
    stray ``.git`` in a parent (a home directory, say) would silently answer
    questions asked about a folder that is not a repository at all.
    ``GIT_CEILING_DIRECTORIES`` stops that ascent. It names directories git may
    not chdir *into*, and it ignores the starting directory itself, so the entry
    is the parent of the sandbox root -- git stays free to find a repository
    anywhere inside the sandbox, and cannot reach one above it.
    """
    if cwd is None:
        return None

    from jarvis.tools.paths import allowed_roots  # local: paths imports config

    resolved = Path(cwd).resolve()
    boundary = resolved
    for root in allowed_roots():
        if resolved == root or root in resolved.parents:
            boundary = root
            break

    env = dict(os.environ)
    env["GIT_CEILING_DIRECTORIES"] = str(boundary.parent)
    return env


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + "\n... (output truncated)"
