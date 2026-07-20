"""
test_file_tool_max_read_bytes.py
------------------------------------
Tests for FileTool's configurable max_read_bytes — the read-size cap was
previously a hardcoded module constant (_MAX_READ_BYTES = 102400) with no
way to override it per instance.

Run:
    python -m pytest tests/test_file_tool_max_read_bytes.py -x -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.file_tool import FileTool, _MAX_READ_BYTES


@pytest.fixture
def allowed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "allowed"
    d.mkdir()
    return d


class TestDefaultMaxReadBytes:
    def test_default_matches_module_constant(self, allowed_dir):
        tool = FileTool(allowed_paths=[allowed_dir])
        assert tool._max_read_bytes == _MAX_READ_BYTES

    def test_reads_full_content_under_default_cap(self, allowed_dir):
        f = allowed_dir / "small.txt"
        f.write_text("hello world")
        tool = FileTool(allowed_paths=[allowed_dir])
        assert tool.read_file(str(f)) == "hello world"


class TestOverriddenMaxReadBytes:
    def test_instance_uses_override_value(self, allowed_dir):
        tool = FileTool(allowed_paths=[allowed_dir], max_read_bytes=10)
        assert tool._max_read_bytes == 10

    def test_truncates_at_overridden_cap(self, allowed_dir):
        f = allowed_dir / "big.txt"
        f.write_text("0123456789ABCDEF")  # 16 bytes
        tool = FileTool(allowed_paths=[allowed_dir], max_read_bytes=10)
        content = tool.read_file(str(f))
        assert content.startswith("0123456789")
        assert "truncated" in content

    def test_does_not_truncate_content_at_or_under_overridden_cap(self, allowed_dir):
        f = allowed_dir / "exact.txt"
        f.write_text("0123456789")  # exactly 10 bytes
        tool = FileTool(allowed_paths=[allowed_dir], max_read_bytes=10)
        assert tool.read_file(str(f)) == "0123456789"
