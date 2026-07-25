"""
tests/test_agent_discovery.py
----------------------------
Tests for auto-discovery of agent modules.

Run:
    python -m pytest tests/test_agent_discovery.py -x -q
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from core.agent_discovery import _extract_agent_class_names, _find_agent_modules


class TestFindAgentModules:
    def test_finds_all_agent_modules(self):
        modules = _find_agent_modules()
        # Should find at least the 5 existing agents
        assert len(modules) >= 5
        
        module_names = {p.parent.name for p in modules}
        assert "business" in module_names
        assert "devops" in module_names
        assert "projects" in module_names
        assert "wellbeing" in module_names
        assert "echo" in module_names

    def test_returns_sorted_paths(self):
        modules = _find_agent_modules()
        module_names = [p.parent.name for p in modules]
        assert module_names == sorted(module_names)


class TestExtractAgentClassNames:
    def test_extracts_business_agent(self):
        agent_path = Path("agents/business/agent.py")
        if agent_path.exists():
            names = _extract_agent_class_names(agent_path)
            assert "BusinessAgent" in names

    def test_extracts_echo_agent(self):
        agent_path = Path("agents/echo/agent.py")
        if agent_path.exists():
            names = _extract_agent_class_names(agent_path)
            assert "EchoAgent" in names

    def test_ignores_files_without_base_agent(self, tmp_path: Path):
        # Create a fake agent file without BaseAgent
        fake_agent = tmp_path / "fake" / "agent.py"
        fake_agent.parent.mkdir(parents=True)
        fake_agent.write_text("class FakeAgent:\n    pass\n")
        
        names = _extract_agent_class_names(fake_agent)
        assert names == []

    def test_handles_syntax_errors_gracefully(self, tmp_path: Path):
        # Create a file with invalid Python syntax
        bad_file = tmp_path / "bad_agent.py"
        bad_file.write_text("this is not valid python {{{")
        
        names = _extract_agent_class_names(bad_file)
        assert names == []


class TestDiscoverAgents:
    def test_discovers_existing_agents_successfully(self):
        """Integration test: discover_agents should load all existing agents."""
        settings = MagicMock()
        settings.debug_echo_agent = False
        
        bus = MagicMock()
        
        # Create mock dependencies
        storage = MagicMock()
        storage.get_or_create_session = MagicMock(return_value="session_123")
        storage.save_message = MagicMock()
        
        notifier = MagicMock()
        notifier.send = MagicMock()
        
        llm = MagicMock()
        llm.supports_tools = False
        
        memory = MagicMock()
        memory.get_context = MagicMock(return_value="")
        memory.save_message = MagicMock()
        
        safety = MagicMock()
        safety.check_action = MagicMock(return_value=True)
        
        skill_loader = MagicMock()
        
        from core.agent_discovery import discover_agents
        agents, failed = discover_agents(
            settings=settings,
            bus=bus,
            storage=storage,
            notifier=notifier,
            llm=llm,
            memory=memory,
            safety=safety,
            skill_loader=skill_loader,
        )
        
        # Should have discovered at least business, devops, projects, wellbeing
        agent_names = {a.name for a in agents}
        assert "business" in agent_names
        assert "devops" in agent_names
        assert "projects" in agent_names
        assert "wellbeing" in agent_names
        assert "echo" not in agent_names  # Skipped due to routable=False and debug_echo_agent=False

    def test_includes_echo_when_debug_enabled(self):
        """EchoAgent should be discovered when DEBUG_ECHO_AGENT is True."""
        settings = MagicMock()
        settings.debug_echo_agent = True  # Enable echo agent
        
        bus = MagicMock()
        
        storage = MagicMock()
        storage.get_or_create_session = MagicMock(return_value="session_123")
        storage.save_message = MagicMock()
        
        notifier = MagicMock()
        llm = MagicMock()
        memory = MagicMock()
        safety = MagicMock()
        skill_loader = MagicMock()
        
        from core.agent_discovery import discover_agents
        agents, failed = discover_agents(
            settings=settings,
            bus=bus,
            storage=storage,
            notifier=notifier,
            llm=llm,
            memory=memory,
            safety=safety,
            skill_loader=skill_loader,
        )
        
        agent_names = {a.name for a in agents}
        assert "echo" in agent_names  # Now included