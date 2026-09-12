from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import get_settings
from jarvis.tools import REGISTRY
from jarvis.tools.paths import PathAccessError, resolve_in_sandbox


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Restrict Jarvis to a throwaway directory tree."""
    monkeypatch.setenv("JARVIS_ALLOWED_ROOTS", str(tmp_path))
    get_settings.cache_clear()

    (tmp_path / "projects" / "jarvis").mkdir(parents=True)
    (tmp_path / "projects" / "jarvis" / "notes.txt").write_text(
        "line one\nline two\nline three\n", encoding="utf-8"
    )
    (tmp_path / "projects" / "report_2024.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 2048)
    (tmp_path / "projects" / "node_modules").mkdir()
    (tmp_path / "projects" / "node_modules" / "notes.txt").write_text("noise")
    (tmp_path / ".env").write_text("SECRET=hunter2", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    yield tmp_path
    get_settings.cache_clear()


def test_paths_outside_the_sandbox_are_refused(sandbox: Path) -> None:
    with pytest.raises(PathAccessError):
        resolve_in_sandbox(r"C:\Windows\System32")


def test_parent_traversal_cannot_escape(sandbox: Path) -> None:
    with pytest.raises(PathAccessError):
        resolve_in_sandbox(str(sandbox / ".." / ".."))


def test_paths_inside_the_sandbox_resolve(sandbox: Path) -> None:
    assert resolve_in_sandbox(str(sandbox / "projects")) == sandbox / "projects"


def test_missing_path_raises_not_found(sandbox: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_in_sandbox(str(sandbox / "nope.txt"))


def test_search_finds_files_and_skips_noise_directories(sandbox: Path) -> None:
    result = REGISTRY.execute("search_files", {"query": "notes"})
    assert result.ok, result.error
    paths = [m["path"] for m in result.data["matches"]]
    assert str(sandbox / "projects" / "jarvis" / "notes.txt") in paths
    assert not any("node_modules" in p for p in paths)


def test_search_supports_globs_and_kind_filter(sandbox: Path) -> None:
    result = REGISTRY.execute(
        "search_files", {"query": "*.pdf", "kind": "file", "max_results": 5}
    )
    assert result.ok, result.error
    assert [m["name"] for m in result.data["matches"]] == ["report_2024.pdf"]


def test_search_rejects_roots_outside_the_sandbox(sandbox: Path) -> None:
    result = REGISTRY.execute("search_files", {"query": "x", "root": "C:\\Windows"})
    assert not result.ok
    assert "outside the allowed roots" in (result.error or "")


def test_search_rejects_empty_query(sandbox: Path) -> None:
    result = REGISTRY.execute("search_files", {"query": "  "})
    assert not result.ok


def test_list_directory_sorts_directories_first(sandbox: Path) -> None:
    result = REGISTRY.execute("list_directory", {"path": str(sandbox / "projects")})
    assert result.ok, result.error
    names = [entry["name"] for entry in result.data["entries"]]
    assert names[0] in ("jarvis", "node_modules")
    assert "report_2024.pdf" in names


def test_read_text_file_returns_content(sandbox: Path) -> None:
    target = sandbox / "projects" / "jarvis" / "notes.txt"
    result = REGISTRY.execute("read_text_file", {"path": str(target)})
    assert result.ok, result.error
    assert result.data["content"].startswith("line one")
    assert result.data["line_count"] == 3


def test_read_text_file_truncates_to_max_lines(sandbox: Path) -> None:
    target = sandbox / "projects" / "jarvis" / "notes.txt"
    result = REGISTRY.execute("read_text_file", {"path": str(target), "max_lines": 1})
    assert result.data["content"] == "line one"
    assert result.data["truncated"] is True


def test_read_text_file_refuses_credential_files(sandbox: Path) -> None:
    result = REGISTRY.execute("read_text_file", {"path": str(sandbox / ".env")})
    assert not result.ok
    assert "credential" in (result.error or "")


def test_read_text_file_refuses_binaries(sandbox: Path) -> None:
    result = REGISTRY.execute("read_text_file", {"path": str(sandbox / "blob.bin")})
    assert not result.ok
    assert "binary" in (result.error or "")


def test_largest_files_respects_threshold(sandbox: Path) -> None:
    result = REGISTRY.execute("largest_files", {"min_mb": 0.001, "max_results": 3})
    assert result.ok, result.error
    sizes = [f["size_mb"] for f in result.data["files"]]
    assert sizes == sorted(sizes, reverse=True)
    assert result.data["files"][0]["name"] == "report_2024.pdf"
