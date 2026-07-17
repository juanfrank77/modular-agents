"""
core/db.py
-----------
Tiny helpers shared by core/storage.py and core/state_store.py, which each
manage their own tables in the same SQLite database file.
"""

from __future__ import annotations


async def apply_encryption_key(db, encryption_key: str) -> None:
    """Apply SQLCipher encryption key to an open aiosqlite connection, if configured."""
    if encryption_key:
        escaped_key = encryption_key.replace("'", "''")
        await db.execute(f"PRAGMA key = '{escaped_key}'")