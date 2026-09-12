"""Application control: launch, close and enumerate visible apps.

Nothing here takes a raw command line from the model. ``open_application``
resolves a *name* through a known-alias table, the Windows ``App Paths``
registry and the Start Menu, so the model can never smuggle in arguments.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import psutil

from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import tool

#: Friendly name -> executable, for apps that are not on PATH by their alias.
APP_ALIASES: dict[str, str] = {
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "vscode": "code.cmd",
    "vs code": "code.cmd",
    "code": "code.cmd",
    "spotify": "spotify.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "outlook": "outlook.exe",
    "snipping tool": "snippingtool.exe",
}

#: Processes that must never be terminated -- killing them destabilises Windows.
PROTECTED_PROCESSES = frozenset(
    {
        "system",
        "system idle process",
        "registry",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "dwm.exe",
        "explorer.exe",
        "ctfmon.exe",
        "fontdrvhost.exe",
        "audiodg.exe",
        "memory compression",
    }
)


class AppNotFoundError(FileNotFoundError):
    """Raised when an application name cannot be resolved to an executable."""


@tool(
    description=(
        "Launch an application by name, e.g. 'notepad', 'chrome', 'vscode', "
        "'spotify'. Resolves the name against installed apps; it never runs a "
        "raw command line."
    ),
    permission=PermissionLevel.LOW_RISK,
    tags=("apps",),
)
def open_application(name: str) -> dict[str, Any]:
    target = resolve_application(name)
    if target.suffix.lower() == ".lnk":
        os.startfile(str(target))  # noqa: S606 - documented Windows API
        pid = None
    else:
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        process = subprocess.Popen(  # noqa: S603 - argv list, never a shell
            [str(target)],
            creationflags=creation_flags,
            close_fds=True,
        )
        pid = process.pid
    return {"launched": name, "executable": str(target), "pid": pid}


@tool(
    description=(
        "Close every running process whose name matches. Terminates gracefully "
        "first. Refuses critical Windows processes. Requires user confirmation."
    ),
    permission=PermissionLevel.CONFIRM_REQUIRED,
    tags=("apps",),
)
def close_application(name: str, force: bool = False) -> dict[str, Any]:
    needle = name.strip().lower().removesuffix(".exe")
    if not needle:
        raise ValueError("name must not be empty")

    self_pids = {os.getpid(), os.getppid()}
    targets: list[psutil.Process] = []
    for proc in psutil.process_iter(["name"]):
        proc_name = (proc.info["name"] or "").lower()
        if needle not in proc_name.removesuffix(".exe"):
            continue
        if proc_name in PROTECTED_PROCESSES:
            raise PermissionError(f"refusing to close protected process: {proc_name}")
        if proc.pid in self_pids:
            continue
        targets.append(proc)

    if not targets:
        return {"closed": [], "message": f"no running process matches '{name}'"}

    for proc in targets:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    gone, alive = psutil.wait_procs(targets, timeout=4)
    killed: list[int] = []
    if alive and force:
        for proc in alive:
            try:
                proc.kill()
                killed.append(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        gone_after, alive = psutil.wait_procs(alive, timeout=2)
        gone.extend(gone_after)

    return {
        "closed": [p.pid for p in gone],
        "force_killed": killed,
        "still_running": [p.pid for p in alive],
        "matched": len(targets),
    }


@tool(
    description=(
        "List applications the user actually has open, by visible window title. "
        "Use this instead of list_processes when asked 'what do I have open?'."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("apps",),
    untrusted_output=True,
)
def list_open_windows(limit: int = 25) -> list[dict[str, Any]]:
    if sys.platform != "win32":  # pragma: no cover - Windows-only feature
        raise RuntimeError("window enumeration is only supported on Windows")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    windows: list[tuple[int, str]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        windows.append((pid.value, buffer.value))
        return True

    user32.EnumWindows(enum_proc(callback), 0)

    limit = max(1, min(limit, 100))
    seen: set[tuple[int, str]] = set()
    rows: list[dict[str, Any]] = []
    for pid, title in windows:
        if (pid, title) in seen:
            continue
        seen.add((pid, title))
        try:
            process = psutil.Process(pid)
            name = process.name()
            memory_mb = round(process.memory_info().rss / (1024**2), 1)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name, memory_mb = "unknown", None
        rows.append(
            {"pid": pid, "process": name, "title": title, "memory_mb": memory_mb}
        )
    rows.sort(key=lambda row: row["memory_mb"] or 0, reverse=True)
    return rows[:limit]


def resolve_application(name: str) -> Path:
    """Map a friendly application name onto an executable or shortcut."""
    cleaned = name.strip().lower()
    if not cleaned:
        raise ValueError("name must not be empty")
    if any(ch in cleaned for ch in '<>|&;"'):
        raise ValueError(f"invalid application name: {name}")

    candidates = [APP_ALIASES.get(cleaned, ""), cleaned]
    if not cleaned.endswith(".exe"):
        candidates.append(f"{cleaned}.exe")

    for candidate in filter(None, candidates):
        found = shutil.which(candidate)
        if found:
            return Path(found)
        registered = _lookup_app_paths(candidate)
        if registered:
            return registered

    shortcut = _find_start_menu_shortcut(cleaned)
    if shortcut:
        return shortcut

    raise AppNotFoundError(
        f"could not find an application named '{name}' on this PC"
    )


def _lookup_app_paths(executable: str) -> Path | None:
    """Consult the Windows ``App Paths`` registry key (how Start/Run resolves)."""
    if sys.platform != "win32":  # pragma: no cover
        return None
    import winreg

    key_path = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        f"\\{executable if executable.endswith('.exe') else executable + '.exe'}"
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        path = Path(str(value).strip('"'))
        if path.exists():
            return path
    return None


@lru_cache(maxsize=1)
def _start_menu_shortcuts() -> list[Path]:
    """Index Start Menu shortcuts once; covers apps that are not on PATH."""
    roots = [
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", ""))
        / "Microsoft/Windows/Start Menu/Programs",
    ]
    shortcuts: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            shortcuts.extend(root.rglob("*.lnk"))
        except OSError:
            continue
    return shortcuts


def _find_start_menu_shortcut(name: str) -> Path | None:
    exact: Path | None = None
    partial: Path | None = None
    for shortcut in _start_menu_shortcuts():
        stem = shortcut.stem.lower()
        if stem == name:
            exact = shortcut
            break
        if partial is None and name in stem:
            partial = shortcut
    return exact or partial
