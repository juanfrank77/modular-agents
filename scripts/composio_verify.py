"""
scripts/composio_verify.py
--------------------------
One-shot LIVE verification for the migrated Composio SDK (composio>=0.30).

Reads COMPOSIO_API_KEY / COMPOSIO_USER_ID from the environment (.env is
loaded via core.config) and:

  1. Lists the connected Gmail toolkit's tool slugs — confirms the exact
     casing GmailTool must call (GMAIL_FETCH_EMAILS vs gmail_fetch_emails).
  2. Optionally resolves a specific slug directly (--check SLUG) — the
     default listing may be a curated subset, so a missing slug in step 1
     is not proof the slug is gone.
  3. Optionally executes a dry fetch of 1 email when --fetch is passed.

NOTE: step 3 talks to the real Gmail account. Run deliberately:

    .venv/bin/python scripts/composio_verify.py                 # list slugs
    .venv/bin/python scripts/composio_verify.py --check GMAIL_SEND_EMAIL
    .venv/bin/python scripts/composio_verify.py --fetch         # + fetch 1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings  # noqa: E402
from core.composio_tool import ComposioTool  # noqa: E402


async def main(fetch: bool, check: str | None) -> int:
    if not settings.composio_api_key:
        print("COMPOSIO_API_KEY is not set — nothing to verify.")
        return 1

    user_id = settings.composio_user_id or "default"
    tool = ComposioTool(api_key=settings.composio_api_key, user_id=user_id)
    print(f"ComposioTool initialised (user_id={user_id!r})")

    if check:
        # Direct resolution bypasses any curated default listing.
        print(f"\nResolving slug directly: {check!r} …")
        schemas = await tool.get_tools(["gmail"])
        if schemas and isinstance(schemas[0], dict) and "error" in schemas[0]:
            print(f"get_tools failed: {schemas[0]['error']}")
            return 1
        try:
            direct = tool._sdk.tools.get(user_id, slug=check)
        except Exception as exc:
            print(f"✗ {check} does NOT resolve: {exc}")
            return 1
        if isinstance(direct, list) and not direct:
            print(f"✗ {check} returns 0 results — the slug does not exist.")
            return 1
        print(f"✓ {check} resolves: {type(direct).__name__}")
        if isinstance(direct, list):
            print(f"  ({len(direct)} result(s))")
        return 0

    schemas = await tool.get_tools(["gmail"])
    if schemas and isinstance(schemas[0], dict) and "error" in schemas[0]:
        print(f"get_tools failed: {schemas[0]['error']}")
        return 1

    slugs = sorted(
        s.get("name") or s.get("slug") or "?" for s in schemas if isinstance(s, dict)
    )
    print(f"\nGmail tool slugs ({len(slugs)}):")
    for slug in slugs:
        print(f"  - {slug}")

    expected = ["GMAIL_FETCH_EMAILS", "GMAIL_SEND_EMAIL", "GMAIL_CREATE_EMAIL_DRAFT"]
    missing = [s for s in expected if s not in slugs]
    if missing:
        print(f"\n⚠️  Slugs GmailTool expects but did NOT appear: {missing}")
        print("   → GmailTool's slugs need updating to the casing above.")
    else:
        print("\n✅ All slugs GmailTool expects are present as-is.")

    if fetch:
        print("\nFetching 1 email via GMAIL_FETCH_EMAILS …")
        result = await tool.execute("GMAIL_FETCH_EMAILS", max_results=1)
        if "error" in result:
            print(f"execute failed: {result['error']}")
            return 1
        print(f"execute OK — keys: {sorted(result.keys())}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true", help="also fetch 1 email")
    parser.add_argument(
        "--check", metavar="SLUG", help="resolve one slug directly, then exit"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.fetch, args.check)))
