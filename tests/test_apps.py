from __future__ import annotations

import sys

import pytest

from jarvis.tools import REGISTRY
from jarvis.tools.apps import (
    PROTECTED_PROCESSES,
    AppNotFoundError,
    resolve_application,
)
from jarvis.tools.permissions import PermissionLevel

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-specific behaviour"
)


@windows_only
def test_known_alias_resolves_to_an_executable() -> None:
    resolved = resolve_application("notepad")
    assert resolved.name.lower() == "notepad.exe"
    assert resolved.exists()


@windows_only
def test_resolution_is_case_insensitive() -> None:
    assert resolve_application("NOTEPAD").exists()


def test_unknown_application_raises() -> None:
    with pytest.raises(AppNotFoundError):
        resolve_application("definitely-not-installed-xyz")


@pytest.mark.parametrize("name", ["notepad & calc", 'foo"bar', "a | b", "x; y"])
def test_shell_metacharacters_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        resolve_application(name)


def test_empty_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_application("   ")


def test_close_application_requires_confirmation() -> None:
    spec = REGISTRY.get("close_application")
    assert spec is not None
    assert spec.permission is PermissionLevel.CONFIRM_REQUIRED


def test_open_application_is_low_risk() -> None:
    spec = REGISTRY.get("open_application")
    assert spec is not None
    assert spec.permission is PermissionLevel.LOW_RISK


@pytest.mark.parametrize("name", ["explorer", "lsass", "csrss.exe", "Services"])
def test_close_refuses_critical_windows_processes(name: str) -> None:
    """Critical processes must be refused before anything is terminated."""
    result = REGISTRY.execute("close_application", {"name": name})
    # Either the process is running and we refuse it, or it is not running here.
    if result.ok:
        assert result.data["closed"] == []
    else:
        assert "protected process" in (result.error or "")


def test_protected_list_covers_the_shell_and_security_processes() -> None:
    assert {"explorer.exe", "lsass.exe", "csrss.exe", "winlogon.exe"} <= (
        PROTECTED_PROCESSES
    )


def test_close_application_reports_no_match_cleanly() -> None:
    result = REGISTRY.execute(
        "close_application", {"name": "zzz-not-a-real-process"}
    )
    assert result.ok, result.error
    assert result.data["closed"] == []


@windows_only
def test_list_open_windows_returns_titled_windows() -> None:
    result = REGISTRY.execute("list_open_windows", {"limit": 5})
    assert result.ok, result.error
    for row in result.data:
        assert row["title"]
        assert isinstance(row["pid"], int)
