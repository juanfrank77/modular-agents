"""
tests/test_orchestrator_agent.py
---------------------------------
Tests for the OrchestratorAgent.

Run:
    python3 -m pytest tests/test_orchestrator_agent.py -x -q
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


from core.protocols import AgentEvent, AgentResponse, EventType, LLMResult


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_settings(**overrides):
    s = MagicMock()
    s.orchestrator_agent_model = overrides.get("model", "")
    s.telegram_allowed_chat_ids = overrides.get("chat_ids", ["123"])
    s.memory_context_dir = overrides.get("memory_context_dir", Path("/tmp"))
    return s


def _make_agent(**overrides):
    from agents.orchestrator.agent import OrchestratorAgent
    settings = _make_settings(**overrides)
    storage = MagicMock()
    notifier = MagicMock()
    notifier.send = AsyncMock()
    llm = MagicMock()
    llm.complete = AsyncMock()
    memory = MagicMock()
    memory.save_message = AsyncMock()
    memory.save_handoff = AsyncMock()
    safety = MagicMock()
    skill_loader = MagicMock()
    skill_loader.find_relevant = AsyncMock(return_value=[])
    bus = MagicMock()
    agent = OrchestratorAgent(
        settings=settings,
        storage=storage,
        notifier=notifier,
        llm=llm,
        memory=memory,
        safety=safety,
        skill_loader=skill_loader,
        bus=bus,
    )
    return agent


# ── Test classes ─────────────────────────────────────────────────────────────

class TestOrchestratorHandle:
    async def test_handle_unauthorized(self):
        agent = _make_agent(chat_ids=["999"])
        event = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="orchestrator",
            chat_id="123",
            text="plan a mission",
        )
        response = await agent.handle(event)
        assert response.text == "Unauthorized."
        assert response.success is False

    async def test_handle_heartbeat_returns_ok(self):
        agent = _make_agent()
        event = AgentEvent(
            type=EventType.HEARTBEAT_TICK,
            agent_name="orchestrator",
            chat_id="123",
        )
        response = await agent.handle(event)
        assert response.text == "HEARTBEAT_OK"
        assert response.agent_name == "orchestrator"

    async def test_handle_agent_message_delegates(self):
        agent = _make_agent()
        event = AgentEvent(
            type=EventType.AGENT_MESSAGE,
            agent_name="orchestrator",
            chat_id="123",
            origin_agent="echo",
            data={"from_agent": "echo", "event": "test"},
            text="hello from echo",
        )
        response = await agent.handle(event)
        assert response.agent_name == "orchestrator"
        assert response.data.get("notification_received") is True

    async def test_handle_calls_llm_with_mission_state_and_skills(self):
        agent = _make_agent()
        agent.llm.complete = AsyncMock(
            return_value=LLMResult(text="plain response")
        )
        event = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="orchestrator",
            chat_id="123",
            text="plan a mission",
        )
        with patch.object(agent, "_read_mission_state", new_callable=AsyncMock, return_value="# Mission State\n- foo") as mock_read, \
             patch.object(agent, "_load_skills_text", new_callable=AsyncMock, return_value="<skill>planning</skill>") as mock_skills:
            response = await agent.handle(event)
        mock_read.assert_called_once()
        mock_skills.assert_called_once_with("plan a mission")
        assert agent.llm.complete.call_count == 1
        call_kwargs = agent.llm.complete.call_args.kwargs
        assert "# Mission State" in call_kwargs["system"]
        assert "planning" in call_kwargs["system"]
        assert response.text == "plain response"

    async def test_handle_extracts_markdown_block_and_writes_state(self):
        agent = _make_agent()
        agent.llm.complete = AsyncMock(
            return_value=LLMResult(
                text="Here is the plan:\n```markdown\n# New State\n- milestone1\n```"
            )
        )
        event = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="orchestrator",
            chat_id="123",
            text="plan a mission",
        )
        with patch.object(agent, "_read_mission_state", new_callable=AsyncMock, return_value="# Mission State\n- foo"), \
             patch.object(agent, "_write_mission_state", new_callable=AsyncMock) as mock_write:
            await agent.handle(event)
        mock_write.assert_called_once_with("# New State\n- milestone1")

    async def test_handle_no_markdown_block_skips_write(self):
        agent = _make_agent()
        agent.llm.complete = AsyncMock(
            return_value=LLMResult(text="no state update here")
        )
        event = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="orchestrator",
            chat_id="123",
            text="plan a mission",
        )
        with patch.object(agent, "_read_mission_state", new_callable=AsyncMock, return_value="# Mission State\n- foo"), \
             patch.object(agent, "_write_mission_state", new_callable=AsyncMock) as mock_write:
            await agent.handle(event)
        mock_write.assert_not_called()


class TestOrchestratorMissionState:
    async def test_read_mission_state_missing(self, tmp_path):
        from agents.orchestrator.agent import OrchestratorAgent
        settings = _make_settings(memory_context_dir=tmp_path)
        storage = MagicMock()
        notifier = MagicMock()
        agent = OrchestratorAgent(
            settings=settings,
            storage=storage,
            notifier=notifier,
        )
        agent._mission_state_path = tmp_path / "missing.md"
        result = await agent._read_mission_state()
        assert result == "# Mission State\n\nNo active mission."

    async def test_read_mission_state_existing(self, tmp_path):
        from agents.orchestrator.agent import OrchestratorAgent
        settings = _make_settings(memory_context_dir=tmp_path)
        storage = MagicMock()
        notifier = MagicMock()
        state_path = tmp_path / "memory" / "context" / "mission-state.md"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("# Mission State\n- active task", encoding="utf-8")
        agent = OrchestratorAgent(
            settings=settings,
            storage=storage,
            notifier=notifier,
        )
        agent._mission_state_path = state_path
        result = await agent._read_mission_state()
        assert result == "# Mission State\n- active task"

    async def test_write_mission_state(self, tmp_path):
        from agents.orchestrator.agent import OrchestratorAgent
        settings = _make_settings(memory_context_dir=tmp_path)
        storage = MagicMock()
        notifier = MagicMock()
        state_path = tmp_path / "memory" / "context" / "mission-state.md"
        agent = OrchestratorAgent(
            settings=settings,
            storage=storage,
            notifier=notifier,
        )
        agent._mission_state_path = state_path
        await agent._write_mission_state("# New State\n- foo")
        assert state_path.exists()
        assert state_path.read_text(encoding="utf-8") == "# New State\n- foo"

    def test_extract_mission_markdown_found(self):
        agent = _make_agent()
        text = "Some preamble\n```markdown\n# State\n- item\n```"
        result = agent._extract_mission_markdown(text)
        assert result == "# State\n- item"

    def test_extract_mission_markdown_missing(self):
        agent = _make_agent()
        result = agent._extract_mission_markdown("no markdown here")
        assert result is None

    def test_extract_mission_markdown_md_variant(self):
        agent = _make_agent()
        text = "```md\n# State\n```"
        result = agent._extract_mission_markdown(text)
        assert result == "# State"


class TestOrchestratorHealthCheck:
    async def test_health_check_success(self):
        agent = _make_agent()
        agent.llm.complete = AsyncMock(return_value=LLMResult(text="pong"))
        assert await agent.health_check() is True

    async def test_health_check_llm_failure(self):
        agent = _make_agent()
        agent.llm.complete = AsyncMock(side_effect=Exception("LLM down"))
        assert await agent.health_check() is False

    async def test_health_check_no_llm_success(self, tmp_path):
        agent = _make_agent()
        agent.llm = None
        agent._mission_state_path = tmp_path / "mission-state.md"
        assert await agent.health_check() is True


# ── Mission execution ─────────────────────────────────────────────────────────

def _make_mission_event():
    return AgentEvent(
        type=EventType.USER_MESSAGE,
        agent_name="orchestrator",
        chat_id="123",
        text="plan a mission",
    )


def _wire_bus(agent, publish_result=None):
    agent.bus = MagicMock()
    agent.bus.publish = AsyncMock(return_value=publish_result)
    agent.bus.registered_agents = ["devops"]
    agent.memory.save_handoff = AsyncMock()


class TestOrchestratorMissionExecution:
    async def _run_handle(self, agent, tmp_path, llm_text):
        agent._mission_state_path = tmp_path / "mission-state.md"
        agent.llm.complete = AsyncMock(return_value=LLMResult(text=llm_text))
        event = _make_mission_event()
        return await agent.handle(event)

    async def test_mission_with_milestones_delegates_serially(self, tmp_path):
        agent = _make_agent()
        _wire_bus(
            agent,
            AgentResponse(text="done", agent_name="devops", success=True),
        )
        llm_text = (
            "Plan:\n```markdown\n"
            "# Mission\n"
            "1. [ ] Do X — assigned to @devops\n"
            "2. [ ] Do Y — assigned to @devops\n"
            "```"
        )
        response = await self._run_handle(agent, tmp_path, llm_text)
        assert agent.bus.publish.await_count == 2
        second_event = agent.bus.publish.await_args_list[1].args[0]
        assert second_event.text == "Do Y"
        assert "Mission Execution" in response.text
        assert agent.memory.save_handoff.await_count == 2
        assert agent.memory.save_handoff.await_args_list[0].args[0] == "devops"
        assert agent.memory.save_handoff.await_args_list[1].args[0] == "devops"

    async def test_milestone_for_unknown_agent_skipped(self, tmp_path):
        agent = _make_agent()
        _wire_bus(
            agent,
            AgentResponse(text="done", agent_name="devops", success=True),
        )
        llm_text = (
            "Plan:\n```markdown\n"
            "# Mission\n"
            "1. [ ] Do X — assigned to @ghost\n"
            "```"
        )
        response = await self._run_handle(agent, tmp_path, llm_text)
        agent.bus.publish.assert_not_awaited()
        assert "unavailable" in response.text

    async def test_milestone_self_assignment_skipped(self, tmp_path):
        agent = _make_agent()
        _wire_bus(
            agent,
            AgentResponse(text="done", agent_name="devops", success=True),
        )
        llm_text = (
            "Plan:\n```markdown\n"
            "# Mission\n"
            "1. [ ] Do X — assigned to @orchestrator\n"
            "```"
        )
        response = await self._run_handle(agent, tmp_path, llm_text)
        agent.bus.publish.assert_not_awaited()
        assert "unavailable" in response.text

    async def test_failed_delegation_marks_failure(self, tmp_path):
        agent = _make_agent()
        _wire_bus(
            agent,
            AgentResponse(text="boom", agent_name="devops", success=False),
        )
        llm_text = (
            "Plan:\n```markdown\n"
            "# Mission\n"
            "1. [ ] Do X — assigned to @devops\n"
            "```"
        )
        response = await self._run_handle(agent, tmp_path, llm_text)
        assert "❌" in response.text
        handoff = agent.memory.save_handoff.await_args_list[0].args[1]
        assert handoff["status"] == "failed"

    async def test_mission_state_checkbox_flipped_on_success(self, tmp_path):
        agent = _make_agent()
        _wire_bus(
            agent,
            AgentResponse(text="done", agent_name="devops", success=True),
        )
        agent._mission_state_path = tmp_path / "mission-state.md"
        milestone_line = "1. [ ] Do X — assigned to @devops"
        agent._mission_state_path.write_text(
            f"# Mission\n{milestone_line}\n", encoding="utf-8"
        )
        llm_text = (
            "Plan:\n```markdown\n"
            f"# Mission\n{milestone_line}\n"
            "```"
        )
        await self._run_handle(agent, tmp_path, llm_text)
        content = agent._mission_state_path.read_text(encoding="utf-8")
        assert "1. [x] Do X — assigned to @devops" in content

    async def test_no_milestones_returns_empty_results(self, tmp_path):
        agent = _make_agent()
        _wire_bus(
            agent,
            AgentResponse(text="done", agent_name="devops", success=True),
        )
        llm_text = (
            "Plan:\n```markdown\n"
            "# Mission\n"
            "- some task without assignment\n"
            "```"
        )
        response = await self._run_handle(agent, tmp_path, llm_text)
        agent.bus.publish.assert_not_awaited()
        assert "Mission Execution" not in response.text

    async def test_fence_variant_md_tag_parsed(self, tmp_path):
        agent = _make_agent()
        _wire_bus(
            agent,
            AgentResponse(text="done", agent_name="devops", success=True),
        )
        llm_text = (
            "Plan:\n```md\n"
            "# Mission\n"
            "- no assignment here\n"
            "```"
        )
        await self._run_handle(agent, tmp_path, llm_text)
        content = agent._mission_state_path.read_text(encoding="utf-8")
        assert "# Mission" in content
