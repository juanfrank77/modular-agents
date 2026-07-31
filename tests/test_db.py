"""
test_db.py
-----------
Tests for core/db.py's shared SQLCipher encryption-key helper, used by
both Storage and StateStore.

Run:
    python3 -m pytest tests/test_db.py -x -q
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.db import apply_encryption_key


class TestApplyEncryptionKey:
    @pytest.mark.asyncio
    async def test_noop_when_key_empty(self, tmp_path: Path):
        db_path = tmp_path / "plain.db"
        async with aiosqlite.connect(str(db_path)) as db:
            await apply_encryption_key(db, "")
            # A plain (unencrypted) DB stays fully usable — proves no PRAGMA
            # key was applied that would otherwise corrupt/lock it.
            await db.execute("CREATE TABLE t (x INTEGER)")
            await db.commit()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_key",
        [
            "key\twith\ttab",
            "key\nwith\nnewline",
            "key\x00with\x00null",
            "key\x7fwith\x7fdel",
            "key with emoji 🔑",
            "clé",
        ],
    )
    async def test_raises_value_error_for_invalid_characters(self, invalid_key: str):
        db = AsyncMock()
        with pytest.raises(ValueError, match="Key contains invalid characters"):
            await apply_encryption_key(db, invalid_key)
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_escapes_single_quotes_in_key(self):
        db = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=("5.0.0",))
        db.execute.return_value = cursor

        await apply_encryption_key(db, "o'brien's-key")

        db.execute.assert_any_await("PRAGMA cipher_version")
        db.execute.assert_any_await("PRAGMA key = 'o''brien''s-key'")

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_sqlcipher_missing(self):
        db = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        db.execute.return_value = cursor

        with pytest.raises(RuntimeError, match="SQLCipher is not installed"):
            await apply_encryption_key(db, "valid-key")

    @pytest.mark.asyncio
    async def test_raises_clear_runtime_error_against_real_plain_sqlite(
        self, tmp_path: Path
    ):
        """Regression pin for #43: the probe pragma must be the real
        SQLCipher pragma name (`cipher_version`, one word, underscore).
        A prior fix attempt typo'd it as "cypher version" (wrong word, a
        space instead of an underscore) — plain sqlite3 doesn't silently
        return no rows for that, it's invalid PRAGMA syntax and raises a
        confusing sqlite3.OperationalError instead of the intended clear
        RuntimeError. Runs against a real (non-SQLCipher) connection, not a
        mock, so the exact pragma string is what's actually verified."""
        db_path = tmp_path / "plain.db"
        async with aiosqlite.connect(str(db_path)) as db:
            with pytest.raises(RuntimeError, match="SQLCipher is not installed"):
                await apply_encryption_key(db, "valid-key")
