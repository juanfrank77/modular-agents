"""
core/agent_creator.py
---------------------
The agent creator wizard. Drives the /new-agent conversation,
collects requirements, calls the LLM to generate code and skills,
writes files to disk, and patches main.py.

Each chat that starts /new-agent gets its own WizardSession stored
in an in-memory dict. Sessions expire after 10 minutes of inactivity.

Usage (from Telegram handler):
    from core.agent_creator import AgentCreator
    creator = AgentCreator(llm=llm, project_root=Path("."))
    response = await creator.handle(chat_id, text)
    # response is a string to send back to the user
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.llm import LLMProvider

log = get_logger("agent_creator")

_SESSION_TIMEOUT = 600  # 10 minutes


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

    def __init__(self, settings, storage, notifier, llm=None,
                 memory=None, safety=None, skill_loader=None): ...

    async def handle(self, event: AgentEvent) -> AgentResponse: ...
    async def register_schedules(self, bus) -> None: ...
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
    "agent_py": "Complete Python source for agents/{module_name}/agent.py. Must import from agents.base, core.protocols, core.logger. Must implement handle(), register_schedules(), health_check(). Follow the exact same pattern as BusinessAgent."
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
    from core.memory import Memory


@dataclass
class {class_name}Tools:
    pass   # Add tool instances here as you implement them


def build_tools(memory: "Memory") -> {class_name}Tools:
    return {class_name}Tools()
'''


# ── Main.py patcher ───────────────────────────

_IMPORT_MARKER = "from agents.echo.agent import EchoAgent"
_REGISTER_MARKER = "bus.register(echo)"


def _patch_main(main_path: Path, module_name: str, class_name: str) -> bool:
    """
    Patches main.py to import and register the new agent.
    Returns True if patched, False if already present or file not found.
    """
    if not main_path.exists():
        log.warning(
            "main.py not found for patching", event="patch_skip", path=str(main_path)
        )
        return False

    content = main_path.read_text(encoding="utf-8")

    import_line = f"from agents.{module_name}.agent import {class_name}"
    if import_line in content:
        log.info(
            "Agent already registered in main.py", event="patch_skip", agent=module_name
        )
        return False

    # Add import after the echo agent import
    content = content.replace(
        _IMPORT_MARKER,
        f"{_IMPORT_MARKER}\n{import_line}",
    )

    # Add instantiation and registration before echo registration
    instantiation = (
        f"\n    {_snake(module_name)} = {class_name}(\n"
        f"        settings=settings, storage=storage, notifier=notifier,\n"
        f"        llm=llm, memory=memory, safety=safety, skill_loader=skill_loader,\n"
        f"    )\n"
        f"    bus.register({_snake(module_name)})\n"
    )

    content = content.replace(
        "    bus.register(echo)",
        f"{instantiation}    bus.register(echo)",
    )

    main_path.write_text(content, encoding="utf-8")
    log.info("main.py patched", event="patch_done", agent=module_name)
    return True


# ── File writer ───────────────────────────────

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
        skill_file = skills_dir / skill["filename"]
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


def _snake(name: str) -> str:
    return _to_snake(name)


# ── AgentCreator ──────────────────────────────

class AgentCreator:
    def __init__(self, llm: "LLMProvider", project_root: Path) -> None:
        self._llm = llm
        self._root = project_root
        self._sessions: dict[str, WizardSession] = {}

    # ── Public entry point ────────────────────

    async def handle(self, chat_id: str, text: str) -> str:
        """
        Process a message from a user in the /new-agent wizard.
        Returns the response string to send back via Telegram.
        """
        # Clean up expired sessions
        self._sessions = {k: v for k, v in self._sessions.items() if not v.expired}

        text = text.strip()

        # Start a new session on /new-agent
        if text.lower() in ("/new-agent", "new-agent"):
            session = WizardSession(chat_id=chat_id)
            self._sessions[chat_id] = session
            return self._ask_name()

        # No active session
        if chat_id not in self._sessions:
            return "No active agent creation session.\nSend /new-agent to start."

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
        return "Something went wrong. Send /new-agent to start over."

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
                "Please try /new-agent again."
            )

        # Write files
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

        # Patch main.py
        patched = _patch_main(self._root / "main.py", module_name, class_name)

        # Clean up session
        del self._sessions[session.chat_id]

        # Build success message
        file_list = "\n".join(f"  ✅ `{f}`" for f in created)
        main_status = (
            "  ✅ Registered in `main.py`"
            if patched
            else "  ⚠️ Already registered in `main.py`"
        )

        return (
            f"🎉 *{class_name} created successfully!*\n\n"
            f"*Files created:*\n{file_list}\n{main_status}\n\n"
            f"*To activate:*\n"
            f"```\nsudo systemctl restart modular-agents\n```\n\n"
            f"Your new agent will be available immediately after restart.\n"
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

        return await self._llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=(
                "You are an expert Python developer. Respond ONLY with valid JSON. "
                "No markdown fences, no explanation, no preamble. "
                "The JSON must be complete and parseable."
            ),
            max_tokens=4096,
        )

    async def _send_progress(self, session: WizardSession, text: str) -> None:
        """Hook for sending intermediate progress messages. No-op here —
        the Telegram handler calls this via the notifier."""
        pass


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
