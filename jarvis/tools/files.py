"""File and folder tools, all constrained to the configured sandbox roots."""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jarvis.tools.paths import (
    default_root,
    describe,
    is_sensitive,
    resolve_in_sandbox,
    should_skip_directory,
)
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import tool

#: Wall-clock budget for one recursive search, so the agent never stalls.
_SEARCH_TIMEOUT_SECONDS = 8.0
_MAX_READ_BYTES = 200_000


@tool(
    description=(
        "Search for files and folders by name under an allowed root. 'query' is "
        "matched case-insensitively as a substring, or as a glob when it contains "
        "* or ?. Use this for questions like 'where is my resume?'."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("files",),
)
def search_files(
    query: str,
    root: str | None = None,
    kind: str = "any",
    max_results: int = 20,
) -> dict[str, Any]:
    if kind not in ("any", "file", "dir"):
        raise ValueError("kind must be 'any', 'file' or 'dir'")
    if not query.strip():
        raise ValueError("query must not be empty")

    start_dir = resolve_in_sandbox(root) if root else default_root()
    if not start_dir.is_dir():
        raise NotADirectoryError(f"not a directory: {start_dir}")

    max_results = max(1, min(max_results, 100))
    matcher = _build_matcher(query)
    matches: list[dict[str, Any]] = []
    deadline = time.monotonic() + _SEARCH_TIMEOUT_SECONDS
    scanned = 0
    timed_out = False

    for dirpath, dirnames, filenames in os.walk(start_dir, topdown=True):
        if time.monotonic() > deadline:
            timed_out = True
            break
        dirnames[:] = [d for d in dirnames if not should_skip_directory(d)]
        scanned += 1

        if kind in ("any", "dir"):
            for name in dirnames:
                if matcher(name):
                    matches.append(describe(Path(dirpath, name)))
        if kind in ("any", "file"):
            for name in filenames:
                if matcher(name):
                    matches.append(describe(Path(dirpath, name)))
        if len(matches) >= max_results:
            break

    return {
        "query": query,
        "root": str(start_dir),
        "directories_scanned": scanned,
        "truncated": len(matches) > max_results or timed_out,
        "timed_out": timed_out,
        "matches": matches[:max_results],
    }


def _build_matcher(query: str):
    lowered = query.lower()
    if any(ch in query for ch in "*?"):
        return lambda name: fnmatch.fnmatch(name.lower(), lowered)
    return lambda name: lowered in name.lower()


@tool(
    description="List the contents of a folder with sizes and modified times.",
    permission=PermissionLevel.READ_ONLY,
    tags=("files",),
)
def list_directory(path: str, max_entries: int = 50) -> dict[str, Any]:
    target = resolve_in_sandbox(path)
    if not target.is_dir():
        raise NotADirectoryError(f"not a directory: {target}")
    max_entries = max(1, min(max_entries, 200))

    entries = sorted(
        target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
    )
    return {
        "path": str(target),
        "total_entries": len(entries),
        "truncated": len(entries) > max_entries,
        "entries": [describe(entry) for entry in entries[:max_entries]],
    }


@tool(
    description=(
        "Read the beginning of a text file (source code, config, logs, notes). "
        "Refuses binaries and credential files."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("files",),
)
def read_text_file(path: str, max_lines: int = 200) -> dict[str, Any]:
    target = resolve_in_sandbox(path)
    if not target.is_file():
        raise IsADirectoryError(f"not a file: {target}")
    if is_sensitive(target):
        raise PermissionError(f"refusing to read credential file: {target.name}")

    raw = target.read_bytes()[:_MAX_READ_BYTES]
    if b"\x00" in raw:
        raise ValueError(f"{target.name} looks like a binary file")

    max_lines = max(1, min(max_lines, 1000))
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return {
        "path": str(target),
        "size_mb": round(target.stat().st_size / (1024**2), 3),
        "line_count": len(lines),
        "truncated": len(lines) > max_lines or len(raw) == _MAX_READ_BYTES,
        "content": "\n".join(lines[:max_lines]),
    }


@tool(
    description=(
        "Find the largest files under a folder -- use when the user asks what is "
        "eating their disk space."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("files",),
)
def largest_files(
    root: str | None = None, max_results: int = 10, min_mb: float = 50.0
) -> dict[str, Any]:
    start_dir = resolve_in_sandbox(root) if root else default_root()
    max_results = max(1, min(max_results, 50))
    threshold = max(0.0, min_mb) * (1024**2)
    deadline = time.monotonic() + _SEARCH_TIMEOUT_SECONDS
    found: list[tuple[int, Path]] = []
    timed_out = False

    for dirpath, dirnames, filenames in os.walk(start_dir, topdown=True):
        if time.monotonic() > deadline:
            timed_out = True
            break
        dirnames[:] = [d for d in dirnames if not should_skip_directory(d)]
        for name in filenames:
            candidate = Path(dirpath, name)
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            if size >= threshold:
                found.append((size, candidate))

    found.sort(key=lambda item: item[0], reverse=True)
    return {
        "root": str(start_dir),
        "timed_out": timed_out,
        "files": [
            {
                "path": str(path),
                "name": path.name,
                "size_mb": round(size / (1024**2), 1),
            }
            for size, path in found[:max_results]
        ],
    }


@tool(
    description=(
        "Open a file or folder in Windows Explorer / its default application. "
        "Use when the user says 'show me' or 'open that folder'."
    ),
    permission=PermissionLevel.LOW_RISK,
    tags=("files",),
)
def open_path(path: str) -> dict[str, Any]:
    target = resolve_in_sandbox(path)
    if sys.platform == "win32":
        if target.is_dir():
            subprocess.Popen(["explorer", str(target)])  # noqa: S603
        else:
            os.startfile(str(target))  # noqa: S606 - documented Windows API
    else:  # pragma: no cover - developer machines only
        subprocess.Popen(["xdg-open", str(target)])  # noqa: S603,S607
    return {"opened": str(target), "is_dir": target.is_dir()}
