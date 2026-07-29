"""
tests/test_agent_creator.py
--------------------------
Tests for the /newagent wizard, focused on the safety gate introduced for
improvement-ideas.md §3 item 4 ("Agent creator executes unvalidated
LLM-generated Python"):

  * `_validate_generated` rejects malformed/syntactically-broken output and
    path-traversing skill filenames before anything is written.
  * The wizard does NOT write on generation; it writes only after the user
    explicitly confirms, and refuses anything but yes/no at that step.
  * A `no` (or cancel) at the confirm step discards with nothing on disk.
  * The write path keeps the path-traversal guard even if validation is
    bypassed.

Run:
    python3 -m pytest tests/test_agent_creator.py -x -q
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from core.agent_creator import (
    AgentCreator,
    WizardSession,
    _validate_generated,
    _write_agent_files,
)
from core.protocols import LLMResult, Message, ToolDef, ToolResultInput


# ── Helpers ────────────────────────────────────


class FakeLLM:
    """Minimal LLM stub shaped to satisfy the LLMProvider protocol."""

    supports_tools = False

    def __init__(self, raw: str) -> None:
        self._raw = raw

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        model: str = "",
        max_tokens: int = 4096,
        tools: list[ToolDef] | None = None,
        tool_result: ToolResultInput | None = None,
        raw_assistant: Any = None,
    ) -> LLMResult:
        return LLMResult(text=self._raw)

    async def summarize(self, messages: list[Message]) -> str:
        return ""


_AGENT_PY = '''
from agents.base import BaseAgent
from core.logger import get_logger

log = get_logger("writing")


class WritingAgent(BaseAgent):
    name = "writing"
    description = "Helps draft and edit written content."
    autonomy_level = "supervised"
    SCHEDULES = []

    async def handle(self, event):
        return await self.reply(event, "hello")

    async def health_check(self):
        return True
'''


def _valid_parsed(module_name: str = "writing", has_tools: bool = False) -> dict:
    """A generated payload that passes _validate_generated."""
    return {
        "agent": {
            "class_name": "WritingAgent",
            "module_name": module_name,
            "description": "Helps draft and edit written content.",
            "system_prompt": "You are a writing assistant. {{context}} {{skills}}",
            "autonomy_level": "supervised",
            "has_tools": has_tools,
            "agent_py": _AGENT_PY,
        },
        "skills": [
            {"filename": "drafting.md", "content": "# SKILL: drafting\\n\\ndrafting.\\n"},
            {"filename": "editing.md", "content": "# SKILL: editing\\n\\nediting.\\n"},
        ],
        "tools_stub": "" if not has_tools else "pass\\n",
    }


def _raw(parsed: dict) -> str:
    """Serialize a generated payload back to the JSON string the LLM returns."""
    return json.dumps(parsed)


async def _drive_to_confirm(
    creator: AgentCreator, chat_id: str = "c1"
) -> str:
    """Push a wizard session through to the confirm step; return its response."""
    await creator.handle(chat_id, "/newagent")
    await creator.handle(chat_id, "writing")          # name (no conflict vs tmp root)
    await creator.handle(chat_id, "Helps me draft and edit written content for my blog.")
    await creator.handle(chat_id, "supervised")        # autonomy
    await creator.handle(chat_id, "no")                # tools
    await creator.handle(chat_id, "drafting")          # skill 1
    return await creator.handle(chat_id, "/done")      # trigger generation -> confirm


def _run(coro):
    return asyncio.run(coro)


# ── _validate_generated ────────────────────────


class TestValidateGenerated:
    def test_valid_payload_passes(self):
        assert _validate_generated(_valid_parsed(), has_tools=False, module_name="writing") == []

    def test_missing_agent_field(self):
        errors = _validate_generated({}, has_tools=False, module_name="writing")
        assert any("agent" in e for e in errors)

    def test_agent_py_syntax_error_is_caught(self):
        parsed = _valid_parsed()
        parsed["agent"]["agent_py"] = "def x(:\n  pass"  # syntax error
        errors = _validate_generated(parsed, has_tools=False, module_name="writing")
        assert any("agent.py: SyntaxError" in e for e in errors)

    def test_module_name_mismatch_rejected(self):
        parsed = _valid_parsed(module_name="evil")
        errors = _validate_generated(parsed, has_tools=False, module_name="writing")
        assert any("module_name" in e for e in errors)

    def test_empty_required_string_rejected(self):
        parsed = _valid_parsed()
        parsed["agent"]["system_prompt"] = ""
        errors = _validate_generated(parsed, has_tools=False, module_name="writing")
        assert any("system_prompt" in e for e in errors)

    def test_skill_path_traversal_rejected(self):
        parsed = _valid_parsed()
        parsed["skills"][0]["filename"] = "../../etc/evil.md"
        errors = _validate_generated(parsed, has_tools=False, module_name="writing")
        assert any("evil.md" in e for e in errors)
        assert any("bare filename" in e for e in errors)

    def test_skill_absolute_path_rejected(self):
        parsed = _valid_parsed()
        parsed["skills"][0]["filename"] = "/etc/passwd.md"
        errors = _validate_generated(parsed, has_tools=False, module_name="writing")
        assert any("passwd.md" in e for e in errors)
        assert any("bare filename" in e for e in errors)

    def test_skill_non_md_extension_rejected(self):
        parsed = _valid_parsed()
        parsed["skills"][0]["filename"] = "agent.py"
        errors = _validate_generated(parsed, has_tools=False, module_name="writing")
        assert any("agent.py" in e and "lowercase" in e for e in errors)

    def test_skills_not_a_list_rejected(self):
        parsed = _valid_parsed()
        parsed["skills"] = "drafting.md"
        errors = _validate_generated(parsed, has_tools=False, module_name="writing")
        assert any("skills" in e for e in errors)

    def test_tools_stub_syntax_error_when_has_tools(self):
        parsed = _valid_parsed(has_tools=True)
        parsed["tools_stub"] = "def (: pass"
        errors = _validate_generated(parsed, has_tools=True, module_name="writing")
        assert any("tools/__init__.py: SyntaxError" in e for e in errors)

    def test_empty_tools_stub_ok_when_no_tools(self):
        parsed = _valid_parsed(has_tools=False)
        parsed["tools_stub"] = ""
        assert _validate_generated(parsed, has_tools=False, module_name="writing") == []


# ── _write_agent_files path safety ──────────────


def test_write_rejects_traversal_filename(tmp_path: Path):
    parsed = _valid_parsed()
    skills = [{"filename": "../escape.md", "content": "x"}]
    with pytest.raises(ValueError):
        _write_agent_files(
            project_root=tmp_path,
            module_name="writing",
            class_name="WritingAgent",
            agent_py=parsed["agent"]["agent_py"],
            skills=skills,
            tools_stub="",
            has_tools=False,
        )
    # Nothing should have created the escaped file outside the agent dir.
    assert not (tmp_path / "escape.md").exists()


def test_write_creates_expected_files(tmp_path: Path):
    parsed = _valid_parsed()
    created = _write_agent_files(
        project_root=tmp_path,
        module_name="writing",
        class_name="WritingAgent",
        agent_py=parsed["agent"]["agent_py"],
        skills=parsed["skills"],
        tools_stub="",
        has_tools=False,
    )
    assert (tmp_path / "agents" / "writing" / "agent.py").exists()
    assert (tmp_path / "agents" / "writing" / "skills" / "drafting.md").exists()
    assert (tmp_path / "agents" / "writing" / "skills" / "editing.md").exists()
    assert any("agent.py" in f for f in created)


# ── Wizard confirm flow ────────────────────────


class TestConfirmFlow:
    def test_generation_does_not_write_and_enters_confirm(self, tmp_path: Path):
        creator = AgentCreator(
            llm=FakeLLM(_raw(_valid_parsed())), project_root=tmp_path
        )
        resp = _run(_drive_to_confirm(creator))
        # Files must NOT exist yet (no write before approval).
        assert not (tmp_path / "agents" / "writing" / "agent.py").exists()
        # Session is parked in the confirm step, not torn down.
        sess = creator._sessions.get("c1")
        assert sess is not None and sess.step == "confirm"
        assert sess.generated is not None
        # Preview is shown ...
        assert "Preview" in resp
        assert "yes" in resp and "no" in resp

    async def _drive_full(self, creator, reply):
        await _drive_to_confirm(creator, "c1")
        return await creator.handle("c1", reply)

    def test_yes_writer_succeeds(self, tmp_path: Path):
        creator = AgentCreator(
            llm=FakeLLM(_raw(_valid_parsed())), project_root=tmp_path
        )
        resp = _run(self._drive_full(creator, "yes"))
        assert "created successfully" in resp
        assert (tmp_path / "agents" / "writing" / "agent.py").exists()
        assert (tmp_path / "agents" / "writing" / "skills" / "drafting.md").exists()
        # Session cleared after a successful write.
        assert "c1" not in creator._sessions

    def test_no_discards_without_writing(self, tmp_path: Path):
        creator = AgentCreator(
            llm=FakeLLM(_raw(_valid_parsed())), project_root=tmp_path
        )
        resp = _run(self._drive_full(creator, "no"))
        assert "cancelled" in resp
        assert "c1" not in creator._sessions
        assert not (tmp_path / "agents" / "writing" / "agent.py").exists()

    def test_non_yesno_re_prompts_and_writes_nothing(self, tmp_path: Path):
        creator = AgentCreator(
            llm=FakeLLM(_raw(_valid_parsed())), project_root=tmp_path
        )
        resp = _run(self._drive_full(creator, "maybe"))
        # Still parked in confirm, nothing written.
        assert "yes" in resp
        assert creator._sessions["c1"].step == "confirm"
        assert not (tmp_path / "agents" / "writing" / "agent.py").exists()

    def test_syntax_err_response_reports_and_writes_nothing(self, tmp_path: Path):
        parsed = _valid_parsed()
        parsed["agent"]["agent_py"] = "def (((("  # syntax error
        creator = AgentCreator(llm=FakeLLM(_raw(parsed)), project_root=tmp_path)
        resp = _run(_drive_to_confirm(creator, "c1"))
        assert "failed validation" in resp
        assert "SyntaxError" in resp
        assert "c1" not in creator._sessions
        assert not (tmp_path / "agents" / "writing").exists()

    def test_traversal_reports_and_writes_nothing(self, tmp_path: Path):
        parsed = _valid_parsed()
        parsed["skills"][0]["filename"] = "../../etc/evil.md"
        creator = AgentCreator(llm=FakeLLM(_raw(parsed)), project_root=tmp_path)
        resp = _run(_drive_to_confirm(creator, "c1"))
        assert "evil.md" in resp
        assert "failed validation" in resp
        assert not (tmp_path / "evil.md").exists()


# ── WizardSession touch ────────────────────────


def test_wizard_session_generated_defaults_none():
    s = WizardSession(chat_id="x")
    assert s.generated is None