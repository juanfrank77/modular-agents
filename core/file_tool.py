"""
core/file_tool.py
-----------------
Safe filesystem access for agents, restricted to a set of allowed paths.

Usage:
    from core.file_tool import FileTool
    from pathlib import Path

    tool = FileTool(allowed_paths=[Path("/home/user/data")])
    tool.write_file("/home/user/data/notes.txt", "hello")
    content = tool.read_file("/home/user/data/notes.txt")
    files = tool.list_files("/home/user/data", pattern="*.txt")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.logger import get_logger

log = get_logger("file_tool")

_MAX_READ_BYTES = 102400  # 100 KB


class FileTool:
    """Filesystem helper with path-restriction enforcement.

    All public methods resolve paths to absolute form and verify they fall
    under at least one of the ``allowed_paths`` entries before touching the
    filesystem.  This prevents directory-traversal and symlink-escape attacks.
    """

    def __init__(self, allowed_paths: list[Path]) -> None:
        self._allowed = [p.resolve() for p in allowed_paths]
        self._cache: dict[str, dict[str, Any]] = {}
        log.debug("FileTool initialised", count=len(self._allowed))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_path(self, path: Path) -> Path:
        """Resolve *path* and assert it is under an allowed root.

        Returns the resolved absolute ``Path`` on success.
        Raises ``PermissionError`` if the resolved path is outside every
        allowed root.
        """
        resolved = path.resolve()
        for allowed in self._allowed:
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue
        raise PermissionError(
            f"Access denied: '{resolved}' is not under any allowed path "
            f"({[str(a) for a in self._allowed]})"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_files(self, folder: str | Path, pattern: str = "*") -> list[str]:
        """Return file paths matching *pattern* inside *folder*.

        Only files are returned (directories are excluded).  Paths are
        returned as strings relative to *folder*.

        Args:
            folder: Directory to search.  Must be within an allowed path.
            pattern: Glob pattern (e.g. ``"*.txt"``).  Defaults to ``"*"``.

        Returns:
            Sorted list of matching paths as strings relative to *folder*.
        """
        folder_path = self._validate_path(Path(folder))
        if not folder_path.exists():
            log.warning("list_files folder does not exist", folder=str(folder_path))
            raise FileNotFoundError(f"Directory not found: '{folder_path}'")
        if not folder_path.is_dir():
            log.warning("list_files path is not a directory", folder=str(folder_path))
            raise NotADirectoryError(f"Path is not a directory: '{folder_path}'")
        results: list[str] = []
        for item in folder_path.glob(pattern):
            if item.is_file():
                results.append(str(item.relative_to(folder_path)))
        results.sort()
        log.debug(
            "list_files", folder=str(folder_path), pattern=pattern, count=len(results)
        )
        return results

    def read_file(self, path: str | Path) -> str:
        """Read and return the content of *path*.

        If the file exceeds 100 KB only the first 100 KB is returned and a
        truncation notice is appended.

        Caching: Files are cached after reading. Subsequent reads check if the
        file's mtime/size has changed - if not, the cached content is returned.

        Args:
            path: File to read.  Must be within an allowed path.

        Returns:
            File content as a string, optionally with a truncation notice.
        """
        resolved = self._validate_path(Path(path))
        if not resolved.is_file():
            log.warning("read_file path is not a file", path=str(resolved))
            raise ValueError(f"Path is not a file: '{resolved}'")

        resolved_str = str(resolved)
        current_stat = resolved.stat()
        current_mtime = current_stat.st_mtime
        current_size = current_stat.st_size

        if resolved_str in self._cache:
            cached = self._cache[resolved_str]
            if cached["mtime"] == current_mtime and cached["size"] == current_size:
                log.debug("read_file cache hit", path=resolved_str)
                return cached["content"]

        raw = resolved.read_bytes()
        if len(raw) > _MAX_READ_BYTES:
            log.warning(
                "read_file exceeds 100 KB, truncating",
                path=str(resolved),
                size=len(raw),
            )
            content = raw[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
            content += f"\n\n[... truncated: file is {len(raw)} bytes, only first {_MAX_READ_BYTES} bytes shown ...]"
            log.debug("read_file", path=resolved_str, chars=len(content))
            return content

        content = raw.decode("utf-8", errors="replace")

        self._cache[resolved_str] = {
            "content": content,
            "mtime": current_mtime,
            "size": current_size,
        }

        log.debug("read_file", path=resolved_str, chars=len(content))
        return content

    def write_file(self, path: str | Path, content: str) -> None:
        """Write *content* to *path*, creating parent directories if needed.

        Args:
            path: Destination file.  Must be within an allowed path.
            content: Text to write (UTF-8).
        """
        resolved = self._validate_path(Path(path))
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        resolved_str = str(resolved)
        if resolved_str in self._cache:
            del self._cache[resolved_str]
        log.debug("write_file", path=resolved_str, chars=len(content))
