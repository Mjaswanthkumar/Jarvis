from __future__ import annotations

from typing import Any

import pytest

from jarvis.tools import REGISTRY, PermissionLevel
from jarvis.tools.registry import ToolRegistry, tool


def test_core_system_tools_are_registered() -> None:
    names = {spec.name for spec in REGISTRY.available()}
    assert {
        "system_health",
        "cpu_info",
        "memory_info",
        "disk_usage",
        "battery_status",
        "network_info",
        "list_processes",
        "search_files",
        "list_directory",
        "read_text_file",
        "largest_files",
        "open_path",
        "open_application",
        "close_application",
        "list_open_windows",
    } <= names


def test_every_tool_declares_a_description_and_permission() -> None:
    for spec in REGISTRY.available():
        assert len(spec.description) > 20, spec.name
        assert isinstance(spec.permission, PermissionLevel), spec.name


def test_state_changing_tools_are_not_read_only() -> None:
    """A tool that touches the machine must be gated above READ_ONLY."""
    mutating = {"open_application", "close_application", "open_path"}
    for name in mutating:
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert spec.permission is not PermissionLevel.READ_ONLY, name


def test_declarations_expose_parameters_schema() -> None:
    declaration = next(
        d for d in REGISTRY.declarations() if d["name"] == "list_processes"
    )
    schema = declaration["parameters"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"sort_by", "limit", "name_contains"}
    # Optional arguments must not be advertised as required.
    assert not schema.get("required")


def test_system_health_returns_live_values() -> None:
    result = REGISTRY.execute("system_health")
    assert result.ok, result.error
    data: dict[str, Any] = result.data
    assert 0 <= data["cpu"]["percent"] <= 100
    assert data["memory"]["total_gb"] > 0
    assert isinstance(data["disks"], list)


def test_list_processes_respects_limit_and_sort() -> None:
    result = REGISTRY.execute("list_processes", {"sort_by": "memory", "limit": 3})
    assert result.ok, result.error
    rows = result.data
    assert 0 < len(rows) <= 3
    assert rows == sorted(rows, key=lambda r: r["memory_mb"], reverse=True)


def test_unknown_tool_is_reported_not_raised() -> None:
    result = REGISTRY.execute("definitely_not_a_tool")
    assert not result.ok
    assert "unknown tool" in (result.error or "")


def test_invalid_arguments_are_rejected() -> None:
    result = REGISTRY.execute("list_processes", {"sort_by": "banana"})
    assert not result.ok
    assert "sort_by" in (result.error or "")


def test_unexpected_argument_is_rejected() -> None:
    result = REGISTRY.execute("cpu_info", {"nope": 1})
    assert not result.ok
    assert "invalid arguments" in (result.error or "")


def test_tool_exceptions_become_error_results() -> None:
    registry = ToolRegistry()

    @tool(
        description="always fails",
        permission=PermissionLevel.READ_ONLY,
        registry=registry,
    )
    def boom() -> None:
        raise RuntimeError("kaboom")

    result = registry.execute("boom")
    assert not result.ok
    assert "kaboom" in (result.error or "")


def test_blocked_tools_are_never_advertised_or_run() -> None:
    registry = ToolRegistry()

    @tool(
        description="forbidden",
        permission=PermissionLevel.BLOCKED,
        registry=registry,
    )
    def forbidden() -> str:
        return "should never run"

    assert registry.available() == []
    result = registry.execute("forbidden")
    assert not result.ok
    assert "blocked" in (result.error or "")


def test_duplicate_registration_fails_fast() -> None:
    registry = ToolRegistry()

    @tool(description="a", permission=PermissionLevel.READ_ONLY, registry=registry)
    def dupe() -> int:
        return 1

    with pytest.raises(ValueError):

        @tool(description="b", permission=PermissionLevel.READ_ONLY, registry=registry)
        def dupe() -> int:  # noqa: F811
            return 2
