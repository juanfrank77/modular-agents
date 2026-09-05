"""
tests/test_agent_profiles.py
----------------------------
Tests for structured agent profiles (10.1).

Run:
    python -m pytest tests/test_agent_profiles.py -x -q
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.echo.agent import EchoAgent
from core.protocols import AgentProfile
from core.state_store import StateStore


# ── StateStore CRUD ───────────────────────────────────────────────────────────


@pytest.fixture
async def store(tmp_path: Path) -> StateStore:
    s = StateStore(tmp_path / "state.db")
    await s.init()
    return s


class TestAgentProfiles:
    async def test_save_and_load(self, store: StateStore):
        profile = AgentProfile(
            name="test",
            description="A test agent",
            emoji="🧪",
            autonomy_level="supervised",
            routable=True,
            enabled=True,
        )
        await store.save_agent_profile(profile)
        loaded = await store.load_agent_profile("test")
        assert loaded is not None
        assert loaded.name == "test"
        assert loaded.description == "A test agent"
        assert loaded.emoji == "🧪"
        assert loaded.autonomy_level == "supervised"
        assert loaded.routable is True
        assert loaded.enabled is True

    async def test_overwrite_preserves_created_at(self, store: StateStore):
        profile = AgentProfile(name="test", description="v1", created_at="2024-01-01T00:00:00+00:00", updated_at="2024-01-01T00:00:00+00:00")
        await store.save_agent_profile(profile)
        profile.description = "v2"
        await store.save_agent_profile(profile)
        loaded = await store.load_agent_profile("test")
        assert loaded is not None
        assert loaded.description == "v2"
        assert loaded.created_at == "2024-01-01T00:00:00+00:00"
        assert loaded.updated_at != "2024-01-01T00:00:00+00:00"

    async def test_load_missing_returns_none(self, store: StateStore):
        assert await store.load_agent_profile("nonexistent") is None

    async def test_load_all(self, store: StateStore):
        await store.save_agent_profile(AgentProfile(name="a", description="A"))
        await store.save_agent_profile(AgentProfile(name="b", description="B"))
        all_profiles = await store.load_all_agent_profiles()
        assert len(all_profiles) == 2
        names = {p.name for p in all_profiles}
        assert names == {"a", "b"}

    async def test_delete(self, store: StateStore):
        await store.save_agent_profile(AgentProfile(name="test", description="A"))
        await store.delete_agent_profile("test")
        assert await store.load_agent_profile("test") is None

    async def test_defaults_applied(self, store: StateStore):
        await store.save_agent_profile(AgentProfile(name="minimal", description="minimal"))
        loaded = await store.load_agent_profile("minimal")
        assert loaded is not None
        assert loaded.emoji == "🤖"
        assert loaded.autonomy_level == "supervised"
        assert loaded.routable is True
        assert loaded.enabled is True


# ── BaseAgent profile methods ─────────────────────────────────────────────────


def _make_echo_agent(state_store=None):
    settings = MagicMock()
    settings.echo_agent_model = ""
    settings.telegram_allowed_chat_ids = []
    notifier = MagicMock()
    notifier.send = AsyncMock()
    return EchoAgent(
        settings=settings,
        storage=MagicMock(),
        notifier=notifier,
        state_store=state_store,
    )


class TestBaseAgentProfile:
    async def test_ensure_profile_seeds_from_class_defaults(self):
        agent = _make_echo_agent()
        profile = await agent.ensure_profile()
        assert profile.name == "echo"
        assert profile.description == "Echoes messages back. Used to validate the Phase 1 stack."
        assert profile.emoji == "🤖"
        assert profile.autonomy_level == "read_only"
        assert profile.routable is False
        assert profile.enabled is True

    async def test_ensure_profile_persists_when_state_store_present(self, tmp_path: Path):
        store = StateStore(tmp_path / "profiles.db")
        await store.init()
        agent = _make_echo_agent(state_store=store)
        profile = await agent.ensure_profile()
        assert profile.created_at != ""
        assert profile.updated_at != ""
        loaded = await store.load_agent_profile("echo")
        assert loaded is not None
        assert loaded.name == "echo"

    async def test_ensure_profile_loads_existing(self, tmp_path: Path):
        store = StateStore(tmp_path / "profiles.db")
        await store.init()
        await store.save_agent_profile(AgentProfile(name="echo", description="OVERRIDDEN", emoji="🔊", autonomy_level="autonomous", routable=True, enabled=True))
        agent = _make_echo_agent(state_store=store)
        profile = await agent.ensure_profile()
        assert profile.description == "OVERRIDDEN"
        assert profile.emoji == "🔊"
        assert profile.autonomy_level == "autonomous"
        assert profile.routable is True

    async def test_get_profile_returns_cached(self):
        agent = _make_echo_agent()
        first = await agent.get_profile()
        second = await agent.get_profile()
        assert first is second

    async def test_save_profile_writes_changes(self, tmp_path: Path):
        store = StateStore(tmp_path / "profiles.db")
        await store.init()
        agent = _make_echo_agent(state_store=store)
        await agent.ensure_profile()
        agent._profile.emoji = "🔄"
        await agent.save_profile()
        loaded = await store.load_agent_profile("echo")
        assert loaded is not None
        assert loaded.emoji == "🔄"
