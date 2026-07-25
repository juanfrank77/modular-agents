"""
test_file_tool_write_and_cache.py
----------------------------------
Tests for FileTool.write_file's size cap and atomicity, and the bounded
read cache — all previously open items (no size cap, non-atomic writes,
unbounded cache).

Run:
    python -m pytest tests/test_file_tool_write_and_cache.py -x -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.file_tool import FileTool, _MAX_WRITE_BYTES


@pytest.fixture
def allowed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "allowed"
    d.mkdir()
    return d


class TestMaxWriteBytes:
    def test_default_matches_module_constant(self, allowed_dir):
        tool = FileTool(allowed_paths=[allowed_dir])
        assert tool._max_write_bytes == _MAX_WRITE_BYTES

    def test_write_under_cap_succeeds(self, allowed_dir):
        tool = FileTool(allowed_paths=[allowed_dir], max_write_bytes=10)
        tool.write_file(str(allowed_dir / "small.txt"), "0123456789")
        assert (allowed_dir / "small.txt").read_text() == "0123456789"

    def test_write_over_cap_rejected(self, allowed_dir):
        tool = FileTool(allowed_paths=[allowed_dir], max_write_bytes=10)
        with pytest.raises(ValueError, match="too large"):
            tool.write_file(str(allowed_dir / "big.txt"), "0123456789ABCDEF")
        assert not (allowed_dir / "big.txt").exists()


class TestAtomicWrite:
    def test_no_leftover_temp_files_after_write(self, allowed_dir):
        tool = FileTool(allowed_paths=[allowed_dir])
        target = allowed_dir / "note.txt"
        tool.write_file(str(target), "hello")
        assert sorted(os.listdir(allowed_dir)) == ["note.txt"]

    def test_failed_write_does_not_corrupt_existing_file(self, allowed_dir, monkeypatch):
        target = allowed_dir / "existing.txt"
        target.write_text("original")
        tool = FileTool(allowed_paths=[allowed_dir])

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            tool.write_file(str(target), "new content")

        assert target.read_text() == "original"
        assert sorted(os.listdir(allowed_dir)) == ["existing.txt"]


class TestBoundedReadCache:
    def test_cache_evicts_oldest_beyond_max_entries(self, allowed_dir):
        tool = FileTool(allowed_paths=[allowed_dir], max_cache_entries=2)
        for i in range(3):
            f = allowed_dir / f"f{i}.txt"
            f.write_text(f"content-{i}")
            tool.read_file(str(f))

        assert len(tool._cache) == 2
        assert str((allowed_dir / "f0.txt").resolve()) not in tool._cache
        assert str((allowed_dir / "f2.txt").resolve()) in tool._cache

    def test_reading_cached_entry_refreshes_its_recency(self, allowed_dir):
        tool = FileTool(allowed_paths=[allowed_dir], max_cache_entries=2)
        f0, f1, f2 = (allowed_dir / f"f{i}.txt" for i in range(3))
        for f in (f0, f1):
            f.write_text(f.name)
            tool.read_file(str(f))

        tool.read_file(str(f0))  # touch f0 so it's most-recently-used
        f2.write_text(f2.name)
        tool.read_file(str(f2))  # should evict f1, not f0

        assert str(f0.resolve()) in tool._cache
        assert str(f1.resolve()) not in tool._cache
