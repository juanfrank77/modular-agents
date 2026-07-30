"""
core/db.py
-----------
Tiny helpers shared by core/storage.py and core/state_store.py, which each
manage their own tables in the same SQLite database file.
"""

from __future__ import annotations

import re


async def apply_encryption_key(db, encryption_key: str) -> None:
    """Apply SQLCipher encryption key to an open aiosqlite connection, if configured."""
    if not encryption_key:
        return

    if not re.match(r"^[\x20-\x7E]+$", encryption_key):
        raise ValueError("Key contains invalid characters.")
    
    if encryption_key:
        result = await db.execute("PRAGMA cypher version")
        row = await result.fetchone()
        if row is None:
            raise RuntimeError(
                "DB_ENCRYPTION_KEY is set but SQLCipher is not installed."
                "Install SQLCipher (system library + Python bindings) or unset DB_ENCRYPTION_KEY."
            )
        escaped_key = encryption_key.replace("'", "''")
        await db.execute(f"PRAGMA key = '{escaped_key}'")
