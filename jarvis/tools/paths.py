"""Path sandbox: every filesystem tool resolves through here.

Jarvis may only look inside ``JARVIS_ALLOWED_ROOTS`` (the user's home directory
by default). Anything outside -- ``C:\\Windows``, another user's profile, a
``..`` escape -- is refused before the tool body ever runs.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.config import get_settings


class PathAccessError(PermissionError):
    """Raised when a path falls outside the configured allowed roots."""


#: Directories that are noise or hazards during a recursive search.
SKIP_DIRECTORY_NAMES = frozenset(
    {
        "$recycle.bin",
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        "node_modules",
        "system volume information",
        "windows",
        "winsxs",
        ".cache",
        ".gradle",
        ".nuget",
        "appdata",
    }
)

#: Files Jarvis refuses to read even inside an allowed root.
_SENSITIVE_NAMES = frozenset({".env", "id_rsa", "id_ed25519", ".npmrc", ".pypirc"})


def allowed_roots() -> list[Path]:
    return get_settings().allowed_roots


def resolve_in_sandbox(path: str | Path, *, must_exist: bool = True) -> Path:
    """Resolve ``path`` and assert it lives under an allowed root."""
    try:
        candidate = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise PathAccessError(f"cannot resolve path: {path}") from exc

    if not _is_within_allowed(candidate):
        raise PathAccessError(
            f"'{candidate}' is outside the allowed roots "
            f"({', '.join(str(r) for r in allowed_roots())})"
        )
    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"no such file or directory: {candidate}")
    return candidate


def default_root() -> Path:
    return allowed_roots()[0]


def is_sensitive(path: Path) -> bool:
    """True for files whose contents Jarvis should never echo back."""
    name = path.name.lower()
    return name in _SENSITIVE_NAMES or name.endswith((".pem", ".key", ".pfx"))


def should_skip_directory(name: str) -> bool:
    lowered = name.lower()
    return lowered in SKIP_DIRECTORY_NAMES or lowered.startswith("$")


def _is_within_allowed(candidate: Path) -> bool:
    for root in allowed_roots():
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def describe(path: Path) -> dict[str, object]:
    """Uniform metadata dict for a file or directory entry."""
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "name": path.name, "error": "unreadable"}
    from datetime import datetime, timezone

    return {
        "path": str(path),
        "name": path.name,
        "is_dir": path.is_dir(),
        "size_mb": round(stat.st_size / (1024**2), 3) if path.is_file() else None,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
