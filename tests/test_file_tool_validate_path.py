"""
test_file_tool_validate_path.py
----------------------------------
Tests for FileTool._validate_path — the sole gate preventing
directory-traversal and symlink-escape attacks on every FileTool method.

Run:
    python -m pytest tests/test_file_tool_validate_path.py -x -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.file_tool import FileTool


@pytest.fixture
def allowed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "allowed"
    d.mkdir()
    return d


@pytest.fixture
def tool(allowed_dir: Path) -> FileTool:
    return FileTool(allowed_paths=[allowed_dir])


class TestValidatePathAllowsWithinRoot:
    def test_path_inside_allowed_root_is_returned_resolved(self, tool, allowed_dir):
        target = allowed_dir / "notes.txt"
        resolved = tool._validate_path(target)
        assert resolved == target.resolve(strict=False)

    def test_nested_path_inside_allowed_root_is_allowed(self, tool, allowed_dir):
        target = allowed_dir / "sub" / "dir" / "file.txt"
        resolved = tool._validate_path(target)
        assert resolved == target.resolve(strict=False)

    def test_allowed_root_itself_is_allowed(self, tool, allowed_dir):
        resolved = tool._validate_path(allowed_dir)
        assert resolved == allowed_dir.resolve(strict=False)

    def test_nonexistent_path_under_allowed_root_is_allowed(self, tool, allowed_dir):
        # strict=False means non-existent paths must still resolve, not raise.
        target = allowed_dir / "does" / "not" / "exist.txt"
        resolved = tool._validate_path(target)
        assert resolved == target.resolve(strict=False)


class TestValidatePathBlocksEscape:
    def test_path_outside_allowed_root_raises_permission_error(self, tool, tmp_path):
        outside = tmp_path / "elsewhere" / "secret.txt"
        with pytest.raises(PermissionError):
            tool._validate_path(outside)

    def test_directory_traversal_via_dotdot_is_blocked(self, tool, allowed_dir):
        escape = allowed_dir / ".." / ".." / "etc" / "passwd"
        with pytest.raises(PermissionError):
            tool._validate_path(escape)

    def test_sibling_directory_with_shared_prefix_is_blocked(self, tool, allowed_dir):
        # "allowed-evil" starts with the same string as "allowed" but is a
        # different directory — string-prefix matching would wrongly allow
        # this; relative_to() correctly rejects it.
        sibling = allowed_dir.parent / (allowed_dir.name + "-evil") / "file.txt"
        with pytest.raises(PermissionError):
            tool._validate_path(sibling)

    def test_symlink_escaping_allowed_root_is_blocked(self, tool, allowed_dir, tmp_path):
        outside_target = tmp_path / "outside_target.txt"
        outside_target.write_text("secret")
        symlink = allowed_dir / "escape_link"
        symlink.symlink_to(outside_target)

        with pytest.raises(PermissionError):
            tool._validate_path(symlink)

    def test_permission_error_message_names_the_rejected_path(self, tool, tmp_path):
        outside = tmp_path / "elsewhere.txt"
        with pytest.raises(PermissionError, match=str(outside.resolve(strict=False))):
            tool._validate_path(outside)


class TestValidatePathMultipleAllowedRoots:
    def test_path_under_either_allowed_root_is_allowed(self, tmp_path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        tool = FileTool(allowed_paths=[root_a, root_b])

        assert tool._validate_path(root_a / "x.txt") == (root_a / "x.txt").resolve(strict=False)
        assert tool._validate_path(root_b / "y.txt") == (root_b / "y.txt").resolve(strict=False)

    def test_path_under_neither_allowed_root_is_blocked(self, tmp_path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        tool = FileTool(allowed_paths=[root_a, root_b])

        with pytest.raises(PermissionError):
            tool._validate_path(tmp_path / "c" / "z.txt")
