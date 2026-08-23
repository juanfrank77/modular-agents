"""
core/local_file_tool.py
-----------------------
Sandboxed local file reader for agents. Only files under the configured
``local_file_paths`` are accessible; requests outside those roots are rejected.

Usage:
    from core.local_file_tool import LocalFileTool

    tool = LocalFileTool(allowed_paths=[Path("~/notes")])
    text = await tool.read_file("~/notes/project.md")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.logger import get_logger

log = get_logger("local_file")


class LocalFileAccessError(Exception):
    """Raised when a requested file is outside allowed paths or cannot be read."""


class LocalFileTool:
    """Read local text files from a whitelist of directories."""

    def __init__(self, allowed_paths: list[Path]) -> None:
        self._allowed_paths = [p.expanduser().resolve() for p in allowed_paths]
        log.debug(
            "LocalFileTool initialised",
            allowed_paths=[str(p) for p in self._allowed_paths],
        )

    def _resolve(self, path: str) -> Path:
        """Resolve a user-supplied path and verify it sits under an allowed root."""
        target = Path(path).expanduser().resolve()

        for allowed in self._allowed_paths:
            try:
                target.relative_to(allowed)
            except ValueError:
                continue
            return target

        raise LocalFileAccessError(
            f"Access denied: {path} is not under any configured local_file_paths"
        )

    async def read_file(self, path: str, max_chars: int = 20480) -> dict[str, Any]:
        """
        Read a text file from an allowed path.

        Returns a dict with ``path``, ``content``, and optionally ``truncated``.
        """
        if not self._allowed_paths:
            log.warning(
                "read_file called with no allowed paths configured",
                event="local_file_no_paths",
            )
            return {"path": path, "error": "No local_file_paths configured"}

        try:
            target = self._resolve(path)
        except LocalFileAccessError as e:
            log.warning(
                "Local file access denied",
                event="local_file_denied",
                path=path,
                error=str(e),
            )
            return {"path": path, "error": str(e)}

        if not target.exists():
            log.warning(
                "Local file not found", event="local_file_not_found", path=str(target)
            )
            return {"path": str(target), "error": "File not found"}

        if not target.is_file():
            return {"path": str(target), "error": "Path is not a file"}

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            log.warning(
                "Local file is not UTF-8 text",
                event="local_file_not_text",
                path=str(target),
            )
            return {"path": str(target), "error": "File is not readable UTF-8 text"}
        except OSError as e:
            log.warning(
                "Local file read failed",
                event="local_file_read_error",
                path=str(target),
                error=str(e),
            )
            return {"path": str(target), "error": f"Read failed: {e}"}

        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n[... truncated to fit limit ...]"
            truncated = True

        return {"path": str(target), "content": content, "truncated": truncated}

    async def list_files(self) -> list[dict[str, Any]]:
        """List files available under all allowed paths."""
        files: list[dict[str, Any]] = []
        for allowed in self._allowed_paths:
            if not allowed.exists():
                continue
            for item in allowed.rglob("*"):
                if item.is_file():
                    files.append({
                        "path": str(item),
                        "relative": str(item.relative_to(allowed)),
                        "root": str(allowed),
                    })
        return files

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        """
        Write a UTF-8 text file to an allowed path, creating parent directories
        as needed. Existing files are overwritten.

        Returns a dict with ``path`` and ``bytes_written``, or ``error``.
        """
        if not self._allowed_paths:
            log.warning(
                "write_file called with no allowed paths configured",
                event="local_file_no_paths",
            )
            return {"path": path, "error": "No local_file_paths configured"}

        try:
            target = self._resolve(path)
        except LocalFileAccessError as e:
            log.warning(
                "Local file write access denied",
                event="local_file_write_denied",
                path=path,
                error=str(e),
            )
            return {"path": path, "error": str(e)}

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            log.warning(
                "Local file write failed",
                event="local_file_write_error",
                path=str(target),
                error=str(e),
            )
            return {"path": str(target), "error": f"Write failed: {e}"}

        return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}
