"""
test_db.py
-----------
Tests for core/db.py's shared SQLCipher encryption-key helper, used by
both Storage and StateStore.

Run:
    python -m pytest tests/test_db.py -x -q
"""

from __future__ import annotations

from pathlib import Path

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
    async def test_escapes_single_quotes_in_key(self, tmp_path: Path):
        db_path = tmp_path / "keyed.db"
        async with aiosqlite.connect(str(db_path)) as db:
            # Must not raise — a raw single quote in the key would break an
            # unescaped f-string PRAGMA statement.
            await apply_encryption_key(db, "o'brien's-key")