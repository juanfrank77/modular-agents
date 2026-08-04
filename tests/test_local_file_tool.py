"""
tests/test_local_file_tool.py
-----------------------------
Tests for core/local_file_tool.py — sandboxed local file reading.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.local_file_tool import LocalFileAccessError, LocalFileTool


@pytest.fixture
def tmp_paths(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    nested = allowed / "nested"
    nested.mkdir()
    (nested / "note.txt").write_text("hello")
    (allowed / "binary.bin").write_bytes(b"\xff\xfe")
    (tmp_path / "outside.txt").write_text("secret")
    return allowed, nested


@pytest.mark.asyncio
async def test_read_file_returns_content(tmp_paths):
    allowed, nested = tmp_paths
    tool = LocalFileTool(allowed_paths=[allowed])

    result = await tool.read_file(str(nested / "note.txt"))

    assert result["content"] == "hello"
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_read_file_expands_tilde(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    notes = home / "notes"
    notes.mkdir()
    (notes / "note.txt").write_text("from home")
    monkeypatch.setenv("HOME", str(home))

    tool = LocalFileTool(allowed_paths=[Path("~/notes")])
    result = await tool.read_file("~/notes/note.txt")

    assert result["content"] == "from home"


@pytest.mark.asyncio
async def test_read_file_rejects_path_outside_allowed(tmp_paths):
    allowed, _ = tmp_paths
    tool = LocalFileTool(allowed_paths=[allowed])

    result = await tool.read_file("/etc/passwd")

    assert "error" in result
    assert "Access denied" in result["error"]


@pytest.mark.asyncio
async def test_read_file_rejects_traversal(tmp_paths):
    allowed, _ = tmp_paths
    tool = LocalFileTool(allowed_paths=[allowed])

    result = await tool.read_file(str(allowed / ".." / "outside.txt"))

    assert "error" in result
    assert "Access denied" in result["error"]


@pytest.mark.asyncio
async def test_read_file_missing_file(tmp_paths):
    allowed, _ = tmp_paths
    tool = LocalFileTool(allowed_paths=[allowed])

    result = await tool.read_file(str(allowed / "missing.txt"))

    assert "error" in result
    assert "File not found" in result["error"]


@pytest.mark.asyncio
async def test_read_file_rejects_non_text(tmp_paths):
    allowed, _ = tmp_paths
    tool = LocalFileTool(allowed_paths=[allowed])

    result = await tool.read_file(str(allowed / "binary.bin"))

    assert "error" in result
    assert "not readable UTF-8 text" in result["error"]


@pytest.mark.asyncio
async def test_read_file_truncates_large_files(tmp_paths):
    allowed, _ = tmp_paths
    (allowed / "long.txt").write_text("x" * 100)
    tool = LocalFileTool(allowed_paths=[allowed])

    result = await tool.read_file(str(allowed / "long.txt"), max_chars=50)

    assert result["truncated"] is True
    assert len(result["content"]) < 100
    assert "truncated to fit limit" in result["content"]


@pytest.mark.asyncio
async def test_read_file_without_allowed_paths():
    tool = LocalFileTool(allowed_paths=[])
    result = await tool.read_file("/any/path")

    assert "error" in result
    assert "No local_file_paths configured" in result["error"]


@pytest.mark.asyncio
async def test_list_files(tmp_paths):
    allowed, _ = tmp_paths
    tool = LocalFileTool(allowed_paths=[allowed])

    files = await tool.list_files()

    assert len(files) == 2
    paths = {f["relative"] for f in files}
    assert "nested/note.txt" in paths
    assert "binary.bin" in paths


def test_resolve_raises_on_outside_path():
    tool = LocalFileTool(allowed_paths=[Path("/tmp")])
    with pytest.raises(LocalFileAccessError):
        tool._resolve("/etc/passwd")
