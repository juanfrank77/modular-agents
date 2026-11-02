"""
test_base_agent_authorization.py
--------------------------------
Tests for BaseAgent._is_authorized — the agent-level chat gate.

The pre-fix implementation consulted only the static
``telegram_allowed_chat_ids`` allowlist, which strands HTTP sessions: their
chat_ids are minted per-session (``http_<token8>``) and can never appear in
the static list, so a validly-paired HTTP session was denied with
auth_denied despite holding a Bearer token and a persisted pairing record.
The fix delegates to the pairing layer (``safety.pairing.is_paired``) when a
Safety component is present, with the allowlist-only check kept as a
fallback for agents built without one (test fakes).

Run:
    .venv/bin/python -m pytest tests/test_base_agent_authorization.py -q
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agents.base import BaseAgent


class _StubAgent(BaseAgent):
    name = "stub"
    description = "stub agent for authorization tests"

    async def handle(self, event):  # pragma: no cover — never invoked
        raise NotImplementedError

    async def health_check(self):  # pragma: no cover — never invoked
        return True


def _make_agent(allowed_chat_ids: list[str], safety=None) -> _StubAgent:
    settings = MagicMock()
    settings.telegram_allowed_chat_ids = allowed_chat_ids
    return _StubAgent(
        settings=settings,
        storage=MagicMock(),
        notifier=MagicMock(),
        safety=safety,
    )


class TestDelegatesToPairingLayer:
    def test_http_session_chat_authorized_when_paired(self):
        # http_* chat_ids can never be in the static allowlist — only the
        # pairing layer knows about them.
        safety = MagicMock()
        safety.pairing.is_paired = MagicMock(return_value=True)
        agent = _make_agent(allowed_chat_ids=["123"], safety=safety)

        assert agent._is_authorized("http_abc123") is True
        safety.pairing.is_paired.assert_called_once_with("http_abc123")

    def test_unpaired_unknown_chat_still_denied_via_safety(self):
        # Authorization is not weakened: an unknown chat is denied through
        # the pairing layer just as before.
        safety = MagicMock()
        safety.pairing.is_paired = MagicMock(return_value=False)
        agent = _make_agent(allowed_chat_ids=["123"], safety=safety)

        assert agent._is_authorized("999") is False

    def test_allowlisted_telegram_chat_authorized_through_safety(self):
        # is_paired() consults the allowlist internally, so allowlisted
        # Telegram chats keep working through the delegation path.
        safety = MagicMock()
        safety.pairing.is_paired = MagicMock(return_value=True)
        agent = _make_agent(allowed_chat_ids=["123"], safety=safety)

        assert agent._is_authorized("123") is True


class TestFallbackWithoutSafety:
    def test_allowlist_only_when_safety_absent(self):
        # Test fakes construct agents without a Safety component; the
        # original allowlist-only behaviour must survive as the fallback.
        agent = _make_agent(allowed_chat_ids=["123"], safety=None)

        assert agent._is_authorized("123") is True
        assert agent._is_authorized("999") is False

    def test_empty_allowlist_dev_mode_allows_all(self):
        agent = _make_agent(allowed_chat_ids=[], safety=None)

        assert agent._is_authorized("anything") is True
