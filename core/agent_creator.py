"""
core/agent_creator.py
---------------------
The agent creator wizard. Drives the /newagent conversation,
collects requirements, calls the LLM to generate code and skills,
writes files to disk.

Agents are auto-discovered on startup via core/agent_discovery.py,
so new agents are detected without main.py patching.

Each chat that starts /newagent gets its own WizardSession stored
in an in-memory dict. Sessions expire after 10 minutes of inactivity.

Usage (from main.py):
    from core.agent_creator import AgentCreator
    creator = AgentCreator(llm=llm, project_root=Path("."), notifier=router)
    response = await creator.handle(chat_id, text)
    # response is a string to send back to the user
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from core.logger import get_logger

if TYPE_CHECKING:
    from core.protocols import LLMProvider


class _Notifier(Protocol):
    async def send(self, chat_id: str, text: str) -> None: ...


log = get_logger("agent_creator")

_SESSION_TIMEOUT = 600  # 10 minutes

# Preview shown in the confirm step is capped so the message stays readable on
# Telegram. The full generated source is written to disk only after approval.
_PREVIEW_MAX_LINES = 60
_PREVIEW_MAX_CHARS = 2000

# Skill filenames: lowercase alphanumerics, dashes or underscores ending in .md.
# Prevents path traversal (../, absolute paths, backslashes) since the LLM
# controls these names.
_SKILL_FILENAME_RE = re.compile(r"^[a-z0-9_-]+\.md$")


# ── Wizard state ──────────────────────────────

@dataclass
class WizardSession:
    chat_id: str
    step: str = "ask_name"
    name: str = ""
    purpose: str = ""
    autonomy: str = ""
    has_tools: bool = False
    skills: list[str] = field(default_factory=list)
    last_active: float = field(default_factory=time.time)
    # Validated LLM output held between the preview and the user's approval.
    generated: dict[str, Any] | None = None

    def touch(self) -> None:
        self.last_active = time.time()

    @property
    def expired(self) -> bool:
        return time.time() - self.last_active > _SESSION_TIMEOUT


# ── Prompts ───────────────────────────────────

_GENERATION_PROMPT = """
You are an expert Python developer building a modular AI agent framework.
Generate a complete agent implementation based on these specifications.

Agent specifications:
- Name: {name}
- Module name (snake_case): {module_name}
- Class name (PascalCase): {class_name}
- Purpose: {purpose}
- Autonomy level: {autonomy}
- Has external tools: {has_tools}
- Skills to implement: {skills}

The framework has these base classes and patterns:

BaseAgent (agents/base.py):
```python
class BaseAgent(ABC):
    name: str
    description: str
    autonomy_level: str  # "read_only" | "supervised" | "autonomous"
    SCHEDULES: list[tuple[str, str]] = []  # [(task_name, cron_expr), ...]

    def __init__(self, settings, storage, notifier, llm=None,
                 memory=None, safety=None, skill_loader=None): ...

    async def handle(self, event: AgentEvent) -> AgentResponse: ...
    async def health_check(self) -> bool: ...
    async def reply(self, event, text) -> AgentResponse: ...
    def _is_authorized(self, chat_id) -> bool: ...
```

AgentEvent has: type (EventType enum), agent_name, chat_id, text, data dict
AgentResponse has: text, agent_name, success, data dict
EventType has: USER_MESSAGE, SCHEDULED_TASK, HEARTBEAT_TICK

The system prompt template must include {{context}} and {{skills}} placeholders.

SKILL.md files follow this structure:
```
# SKILL: skill-name

## Trigger
[keywords that activate this skill]

## Purpose
[what this skill does]

## Steps
[numbered steps the agent should follow]

## Output Format
[how the response should be structured]

## Rules
[constraints and edge cases]
```

Respond ONLY with valid JSON in exactly this structure — no preamble,
no markdown fences, no explanation:

{{
  "agent": {{
    "class_name": "WritingAgent",
    "module_name": "writing",
    "description": "One sentence description used by the bus for routing",
    "system_prompt": "Full system prompt template with {{context}} and {{skills}} placeholders. Be specific to this agent's domain.",
    "autonomy_level": "{autonomy}",
    "has_tools": {has_tools_bool},
    "agent_py": "Complete Python source for agents/{module_name}/agent.py. Must import from agents.base, core.protocols, core.logger. Must implement handle(), health_check(). Optionally define SCHEDULES class attribute for scheduled tasks. Follow the exact same pattern as BusinessAgent."
  }},
  "skills": [
    {{
      "filename": "skill-name.md",
      "content": "Complete SKILL.md content following the structure above"
    }}
  ],
  "tools_stub": "Complete Python source for agents/{module_name}/tools/__init__.py if has_tools is true, otherwise empty string"
}}
"""

_TOOLS_STUB_TEMPLATE = '''"""
agents/{module_name}/tools/__init__.py
---------------------------------------
Tool factory for the {class_name}.

Add your tool implementations here following the pattern in
agents/devops/tools/github.py and agents/devops/tools/railway.py.

Each tool should:
  1. Accept a Memory instance for context resolution
  2. Use cli_runner.run_cli() for any CLI calls
  3. Return plain dicts/lists — the agent formats output for display
  4. Raise ToolError on failure (imported from core cli_runner)

Usage:
    from agents.{module_name}.tools import {module_name_title}Tools, build_tools
    tools = build_tools(memory=memory)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.protocols import MemoryStore


@dataclass
class {class_name}Tools:
    pass   # Add tool instances here as you implement them


def build_tools(memory: "MemoryStore") -> {class_name}Tools:
    return {class_name}Tools()
'''


# ── File writer ───────────────────────────────

def _validate_generated(parsed: dict, has_tools: bool, module_name: str) -> list[str]:
    """
    Validate generated output *before* anything is written to disk.

    Returns a list of human-readable error strings; an empty list means ok.
    This checks shape/syntax only — it does NOT execute the code — so callers
    can surface concrete failures to the user without risking a broken import
    on the next restart.

    `ast.parse` is the conventional "validate without executing" tool: it
    catches SyntaxError without importing or running the module. Sandbox-level
    safety against a malicious LLM is out of scope here; the threat this
    addresses is accidental LLM breakage plus the missing user gate.
    """
    errors: list[str] = []

    agent = parsed.get("agent")
    if not isinstance(agent, dict):
        errors.append("missing or non-object 'agent' field")
        return errors

    # Shape checks for required string fields.
    for key in ("class_name", "module_name", "system_prompt", "agent_py"):
        if not isinstance(agent.get(key), str) or not agent[key]:
            errors.append(f"'agent.{key}' must be a non-empty string")

    # module_name must match the wizard-assigned name so the written path
    # can't drift from where auto-discovery will look.
    if agent.get("module_name") != module_name:
        errors.append(
            f"agent.module_name {agent.get('module_name')!r} must be {module_name!r}"
        )

    # Syntax-check the generated Python without executing it.
    agent_py = agent.get("agent_py")
    if isinstance(agent_py, str) and agent_py:
        try:
            ast.parse(agent_py)
        except SyntaxError as e:
            errors.append(f"agent.py: SyntaxError at line {e.lineno}: {e.msg}")

    # tools_stub: validate only when the user said the agent has tools.
    tools_stub = parsed.get("tools_stub", "")
    if has_tools:
        if not isinstance(tools_stub, str):
            errors.append("'tools_stub' must be a string when has_tools is true")
        elif tools_stub:
            # Empty stub falls back to the built-in template; only validate
            # non-empty LLM output.
            try:
                ast.parse(tools_stub)
            except SyntaxError as e:
                errors.append(f"tools/__init__.py: SyntaxError at line {e.lineno}: {e.msg}")

    # Skills: filename must be a safe, .md-only basename; no path traversal.
    skills = parsed.get("skills")
    if not isinstance(skills, list):
        errors.append("'skills' must be a list")
    else:
        for skill in skills:
            if not isinstance(skill, dict):
                errors.append(f"skill entry must be an object, got {type(skill).__name__}")
                continue
            filename = skill.get("filename")
            content = skill.get("content")
            if not isinstance(filename, str) or not filename:
                errors.append("skill: 'filename' must be a non-empty string")
            elif filename != os.path.basename(filename) or "/" in filename or "\\" in filename:
                # Reject any path component — the writer's own basename guard
                # would neutralize it, but we surface it now so the user knows
                # the LLM tried a traversal.
                errors.append(
                    f"skill filename {filename!r} must be a bare filename "
                    "(no path separators or directories)"
                )
            elif not _SKILL_FILENAME_RE.match(filename):
                errors.append(
                    f"skill filename {filename!r} must be lowercase *.md "
                    "(a-z, 0-9, _, - only)"
                )
            if not isinstance(content, str):
                errors.append(f"skill {filename!r}: 'content' must be a string")

    return errors


def _write_agent_files(
    project_root: Path,
    module_name: str,
    class_name: str,
    agent_py: str,
    skills: list[dict],
    tools_stub: str,
    has_tools: bool,
) -> list[str]:
    """
    Write all generated files to disk. Returns list of created paths.
    """
    created: list[str] = []
    agent_dir = project_root / "agents" / module_name

    # agents/<name>/__init__.py
    agent_dir.mkdir(parents=True, exist_ok=True)
    init = agent_dir / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
        created.append(str(init.relative_to(project_root)))

    # agents/<name>/agent.py
    agent_file = agent_dir / "agent.py"
    agent_file.write_text(agent_py, encoding="utf-8")
    created.append(str(agent_file.relative_to(project_root)))

    # agents/<name>/skills/
    skills_dir = agent_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    for skill in skills:
        # Defense against path traversal: reject anything that isn't a clean
        # basename matching the allowed pattern. Validation also runs before
        # this point, but the write path stays safe on its own.
        filename = skill["filename"]
        if not isinstance(filename, str) or filename != os.path.basename(filename):
            raise ValueError(f"unsafe skill filename: {skill['filename']!r}")
        if not _SKILL_FILENAME_RE.match(filename):
            raise ValueError(f"unsafe skill filename: {skill['filename']!r}")
        skill_file = skills_dir / filename
        skill_file.write_text(skill["content"], encoding="utf-8")
        created.append(str(skill_file.relative_to(project_root)))

    # agents/<name>/tools/ (if needed)
    if has_tools:
        tools_dir = agent_dir / "tools"
        tools_dir.mkdir(exist_ok=True)

        tools_init = tools_dir / "__init__.py"
        stub = tools_stub or _TOOLS_STUB_TEMPLATE.format(
            module_name=module_name,
            class_name=class_name,
            module_name_title=module_name.title(),
        )
        tools_init.write_text(stub, encoding="utf-8")
        created.append(str(tools_init.relative_to(project_root)))

        runner_note = tools_dir / "README.md"
        runner_note.write_text(
            f"# {class_name} tools\n\n"
            f"Add tool implementations here.\n"
            f"See `agents/devops/tools/github.py` for the pattern to follow.\n",
            encoding="utf-8",
        )
        created.append(str(runner_note.relative_to(project_root)))

    return created


# ── Helpers ───────────────────────────────────

def _to_snake(name: str) -> str:
    """'My Agent Name' → 'my_agent_name'"""
    return re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")


def _to_pascal(name: str) -> str:
    """'my_agent_name' → 'MyAgentName'"""
    return "".join(w.title() for w in name.split("_"))


# ── AgentCreator ──────────────────────────────

class AgentCreator:
    def __init__(
        self,
        llm: "LLMProvider",
        project_root: Path,
        notifier: "_Notifier | None" = None,
    ) -> None:
        self._llm = llm
        self._root = project_root
        self._sessions: dict[str, WizardSession] = {}
        self._notifier = notifier

    # ── Public entry point ────────────────────

    async def handle(self, chat_id: str, text: str) -> str:
        """
        Process a message from a user in the /newagent wizard.
        Returns the response string to send back via Telegram.
        """
        # Clean up expired sessions
        self._sessions = {k: v for k, v in self._sessions.items() if not v.expired}

        text = text.strip()

        # Start a new session on /newagent
        if text.lower() in ("/newagent", "newagent"):
            session = WizardSession(chat_id=chat_id)
            self._sessions[chat_id] = session
            return self._ask_name()

        # No active session
        if chat_id not in self._sessions:
            return "No active agent creation session.\nSend /newagent to start."

        session = self._sessions[chat_id]
        session.touch()

        # Cancel at any point
        if text.lower() in ("/cancel", "cancel"):
            del self._sessions[chat_id]
            return "Agent creation cancelled."

        # Route to the right step handler
        return await self._step(session, text)

    def is_active(self, chat_id: str) -> bool:
        """Returns True if this chat has an active wizard session."""
        session = self._sessions.get(chat_id)
        return session is not None and not session.expired

    # ── Step router ───────────────────────────

    async def _step(self, session: WizardSession, text: str) -> str:
        if session.step == "ask_name":
            return self._handle_name(session, text)
        if session.step == "ask_purpose":
            return self._handle_purpose(session, text)
        if session.step == "ask_autonomy":
            return self._handle_autonomy(session, text)
        if session.step == "ask_tools":
            return self._handle_tools(session, text)
        if session.step == "ask_skills":
            return await self._handle_skills(session, text)
        if session.step == "confirm":
            return await self._handle_confirm(session, text)
        return "Something went wrong. Send /newagent to start over."

    # ── Step handlers ─────────────────────────

    def _ask_name(self) -> str:
        return (
            "🤖 *New agent wizard*\n\n"
            "What should this agent be called?\n"
            "Use a single lowercase word — this becomes its module name.\n\n"
            "_Examples: writing, research, design, finance, support_\n\n"
            "Send /cancel at any time to abort."
        )

    def _handle_name(self, session: WizardSession, text: str) -> str:
        name = _to_snake(text.split()[0])  # take first word only
        if not name or len(name) < 2:
            return "Please enter a valid name (at least 2 characters)."

        # Check for conflicts with existing agents
        agent_dir = self._root / "agents" / name
        if agent_dir.exists():
            return (
                f"An agent named *{name}* already exists.\n"
                "Please choose a different name."
            )

        session.name = name
        session.step = "ask_purpose"
        return (
            f"Got it — *{name}* agent.\n\n"
            "In one sentence, what does this agent do?\n\n"
            "_Example: Helps me draft, edit, and improve written content "
            "for my newsletter and blog_"
        )

    def _handle_purpose(self, session: WizardSession, text: str) -> str:
        if len(text) < 10:
            return "Please give a bit more detail — at least one full sentence."

        session.purpose = text
        session.step = "ask_autonomy"
        return (
            "Should this agent act freely, or ask before taking actions?\n\n"
            "🤖 *Autonomous* — acts immediately on reads and most writes. "
            "Only asks for truly destructive operations.\n\n"
            "👤 *Supervised* — asks for approval before sending messages, "
            "making API writes, or anything that affects the outside world.\n\n"
            "Reply *autonomous* or *supervised*."
        )

    def _handle_autonomy(self, session: WizardSession, text: str) -> str:
        lower = text.lower()
        if "auto" in lower:
            session.autonomy = "autonomous"
        elif "super" in lower:
            session.autonomy = "supervised"
        else:
            return "Please reply *autonomous* or *supervised*."

        session.step = "ask_tools"
        return (
            "Does this agent need to call any external tools or APIs?\n\n"
            "This could be a CLI tool, a web API, a CMS, file system access, "
            "web search, and so on.\n\n"
            "A tools folder will be scaffolded with clear extension points "
            "if you say yes.\n\n"
            "Reply *yes* or *no*."
        )

    def _handle_tools(self, session: WizardSession, text: str) -> str:
        lower = text.lower()
        if lower in ("yes", "y"):
            session.has_tools = True
        elif lower in ("no", "n"):
            session.has_tools = False
        else:
            return "Please reply *yes* or *no*."

        session.step = "ask_skills"
        return (
            "What should this agent be specifically good at?\n\n"
            "Send each skill as a separate message — describe it in a sentence "
            "or two. When you're done, send */done*.\n\n"
            "_You can add up to 5 skills. Aim for 2–3 to start._\n\n"
            "What's the first skill?"
        )

    async def _handle_skills(self, session: WizardSession, text: str) -> str:
        if text.lower() == "/done":
            if not session.skills:
                return (
                    "Please add at least one skill before finishing.\n"
                    "What should this agent be good at?"
                )
            return await self._generate(session)

        if len(session.skills) >= 5:
            return (
                "You've added 5 skills — that's the maximum for now.\n"
                "Send */done* to generate the agent."
            )

        session.skills.append(text)
        count = len(session.skills)

        if count < 5:
            return (
                f"✓ Skill {count} added.\n\n"
                "Add another skill, or send */done* to generate the agent."
            )
        else:
            return (
                "✓ Skill 5 added (maximum reached).\n\n"
                "Send */done* to generate the agent."
            )

    async def _handle_confirm(self, session: WizardSession, text: str) -> str:
        """Approval gate before writing LLM-generated files to disk."""
        lower = text.lower().strip()
        if lower in ("yes", "y", "ok", "confirm", "/confirm"):
            return await self._confirm_write(session)
        if lower in ("no", "n", "cancel", "/cancel"):
            del self._sessions[session.chat_id]
            return "Agent creation cancelled — nothing was written to disk."
        return (
            "Reply *yes* to write these files and create the agent, "
            "or *no* to cancel without writing anything."
        )

    def _build_preview(
        self,
        session: WizardSession,
        class_name: str,
        parsed: dict,
    ) -> str:
        """Build the confirm-step message: file list + truncated agent.py."""
        module_name = session.name
        agent = parsed["agent"]
        skills = parsed.get("skills", [])

        agent_py = agent.get("agent_py", "")
        preview_lines = agent_py.splitlines()
        truncated = len(preview_lines) > _PREVIEW_MAX_LINES or len(
            agent_py
        ) > _PREVIEW_MAX_CHARS
        preview = agent_py[:_PREVIEW_MAX_CHARS]
        if len(preview_lines) > _PREVIEW_MAX_LINES:
            preview = "\n".join(preview_lines[:_PREVIEW_MAX_LINES])
        if truncated:
            preview += "\n… (truncated — full source written only after approval)"

        lines = [
            f"📝 *Preview for {class_name}*",
            "",
            f"*Name:* {module_name}",
            f"*Description:* {agent.get('description', '(none)')}",
            f"*Autonomy:* {session.autonomy}",
            f"*Tools:* {'yes' if session.has_tools else 'no'}",
            f"*Skills:* {len(skills)} file(s)",
        ]
        for skill in skills:
            fname = os.path.basename(str(skill.get("filename", "?")))
            size = len(str(skill.get("content", "")))
            lines.append(f"  • `{fname}` ({size} chars)")
        lines.append("")
        lines.append("*`agent.py` preview:*")
        lines.append("```python")
        lines.append(preview)
        lines.append("```")
        lines.append("")
        lines.append(
            "⚠️ This code will run with full process privileges on restart.\n"
            "Reply *yes* to write it, or *no* to cancel."
        )
        return "\n".join(lines)

    # ── Generation ────────────────────────────

    async def _generate(self, session: WizardSession) -> str:
        module_name = session.name
        class_name = _to_pascal(module_name) + "Agent"

        await self._send_progress(session, "⚙️ Generating your agent...")

        try:
            raw = await self._call_llm(session, module_name, class_name)
            parsed = _parse_json(raw)
        except Exception as e:
            log.error("LLM generation failed", event="gen_error", error=str(e))
            del self._sessions[session.chat_id]
            return (
                "❌ Generation failed — the LLM returned something unexpected.\n"
                f"Error: {e}\n\n"
                "Please try /newagent again."
            )

        # Validate shape + syntax before anything touches disk.
        errors = _validate_generated(parsed, session.has_tools, module_name)
        if errors:
            log.error(
                "Generated agent failed validation",
                event="gen_validate_error",
                errors=errors,
            )
            del self._sessions[session.chat_id]
            report = "\n".join(f"  • {e}" for e in errors)
            return (
                "❌ Generated agent failed validation — nothing was written.\n\n"
                f"{report}\n\n"
                "The model output was not safe to run. Please try /newagent again."
            )

        # Hold the validated output for the confirm step. Nothing is written yet.
        session.generated = parsed
        session.step = "confirm"
        return self._build_preview(session, class_name, parsed)

    async def _confirm_write(self, session: WizardSession) -> str:
        """
        Write the previously-validated, user-approved files to disk.
        Called only from `_handle_confirm` after the user replies `yes`.
        """
        assert session.generated is not None
        module_name = session.name
        class_name = _to_pascal(module_name) + "Agent"
        parsed = session.generated

        try:
            created = _write_agent_files(
                project_root=self._root,
                module_name=module_name,
                class_name=class_name,
                agent_py=parsed["agent"]["agent_py"],
                skills=parsed["skills"],
                tools_stub=parsed.get("tools_stub", ""),
                has_tools=session.has_tools,
            )
        except Exception as e:
            log.error("File writing failed", event="write_error", error=str(e))
            del self._sessions[session.chat_id]
            return f"❌ Failed to write agent files: {e}"

        # Clean up session
        del self._sessions[session.chat_id]

        return (
            f"🎉 *{class_name} created successfully!*\n\n"
            f"*Files created:*\n" + "\n".join(f"  ✅ `{f}`" for f in created) + "\n\n"
            f"*To activate:*\n"
            f"```\nsudo systemctl restart modular-agents\n```\n\n"
            f"Your new agent will be available immediately after restart.\n"
            f"(It's auto-discovered on startup — no manual main.py patching needed.)\n"
            f"You can refine its behaviour by editing the SKILL.md files "
            f"in `agents/{module_name}/skills/` — no restart needed for skill changes."
        )

    async def _call_llm(
        self,
        session: WizardSession,
        module_name: str,
        class_name: str,
    ) -> str:
        from core.protocols import Message

        skills_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(session.skills))

        prompt = _GENERATION_PROMPT.format(
            name=session.name,
            module_name=module_name,
            class_name=class_name,
            purpose=session.purpose,
            autonomy=session.autonomy,
            has_tools=str(session.has_tools),
            has_tools_bool=str(session.has_tools).lower(),
            skills=skills_text,
        )

        result = await self._llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=(
                "You are an expert Python developer. Respond ONLY with valid JSON. "
                "No markdown fences, no explanation, no preamble. "
                "The JSON must be complete and parseable."
            ),
            max_tokens=4096,
        )
        return result.text

    async def _send_progress(self, session: WizardSession, text: str) -> None:
        if self._notifier is not None:
            await self._notifier.send(session.chat_id, text)


# ── JSON parser ───────────────────────────────

def _parse_json(raw: str) -> dict:
    """
    Parse JSON from LLM output. Strips markdown fences if present.
    Raises ValueError with a clear message if parsing fails.
    """
    # Strip markdown fences
    clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE)
    clean = clean.strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        # Try to find the JSON object if there's surrounding text
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        raise ValueError(
            f"Could not parse LLM response as JSON: {e}\n"
            f"Raw response (first 500 chars): {raw[:500]}"
        ) from e
