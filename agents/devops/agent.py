"""
agents/devops/agent.py
----------------------
The DevOps Agent. Handles infrastructure monitoring, GitHub activity,
deployment pipelines, and incident response. Runs in autonomous autonomy
mode — reads and most writes execute immediately. Only truly destructive
operations (production deploys, deletions) require explicit approval.

Scheduled jobs (registered at startup):
  - Health check:        every 30 minutes (heartbeat)
  - GitHub digest:       weekdays at 9am
  - Incident watchdog:   every 15 minutes

Lifecycle per message:
  1. Authorize chat
  2. Load relevant skills via SkillLoader
  3. Load markdown context + compacted history via Memory
  4. Build system prompt
  5. Call LLM
  6. Check safety for destructive actions only
  7. Execute / respond
  8. Save to memory + solutions if useful
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType, Message
from core.safety import ActionType as SafetyActionType
from core.budget import ActionType
from agents.base import BaseAgent
from agents.devops.tools import DevOpsTools, build_tools
from agents.devops.tools.cli_runner import ToolError

if TYPE_CHECKING:
    from core.bus import MessageBus

log = get_logger("devops")

_SKILLS_DIR = Path(__file__).parent / "skills"

_SYSTEM_TEMPLATE = """\
You are a senior DevOps engineer assistant. Your job is to monitor infrastructure,
manage GitHub workflows, assist with deployments, and respond to incidents quickly.

You are terse, precise, and technical. You speak in facts, not filler.
When something is broken, you say what it is and how to fix it.
When something needs a decision, you present options with tradeoffs — not opinions.

Autonomy level: autonomous
- You may read, search, query APIs, run diagnostics, and write low-risk configs freely.
- Before any production deploy, database migration, or resource deletion, you MUST
  describe the action and wait for approval. Format as:
    ACTION: <type> | <description>
  Example: ACTION: DEPLOY_PROD | Deploy v1.4.2 to production via Fly.io

{context}

{skills}
"""

# Action types specific to DevOps — maps to safety.ActionType
_ACTION_MAP = {
    "DEPLOY_PROD": SafetyActionType.DESTRUCTIVE,
    "DEPLOY_STAGING": SafetyActionType.WRITE_HIGH,
    "DB_MIGRATE": SafetyActionType.DESTRUCTIVE,
    "DB_ROLLBACK": SafetyActionType.DESTRUCTIVE,
    "DELETE_RESOURCE": SafetyActionType.DESTRUCTIVE,
    "RESTART_SERVICE": SafetyActionType.WRITE_HIGH,
    "MERGE_PR": SafetyActionType.WRITE_HIGH,
    "CLOSE_ISSUE": SafetyActionType.WRITE_LOW,
    "CREATE_ISSUE": SafetyActionType.WRITE_LOW,
    "RUN_SCRIPT": SafetyActionType.EXECUTE,
    "READ": SafetyActionType.READ,
    "SEARCH": SafetyActionType.READ,
    "QUERY": SafetyActionType.READ,
}


class DevOpsAgent(BaseAgent):
    name = "devops"
    description = (
        "Handles DevOps work: GitHub monitoring, deployment pipelines, "
        "infrastructure health checks, incident response, and system diagnostics."
    )
    autonomy_level = "autonomous"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tools: DevOpsTools | None = None

    @property
    def tools(self) -> DevOpsTools:
        if self._tools is None:
            assert self.memory is not None, (
                "Memory must be injected before accessing tools"
            )
            self._tools = build_tools(memory=self.memory)
        return self._tools

    # ── Main handler ──────────────────────────

    async def handle(self, event: AgentEvent) -> AgentResponse:
        if not self._is_authorized(event.chat_id):
            log.warning(
                "Unauthorised access", event="auth_denied", chat_id=event.chat_id
            )
            return AgentResponse(
                text="Unauthorized.", agent_name=self.name, success=False
            )

        # Cross-agent messages handling
        if event.type == EventType.AGENT_MESSAGE:
            return await self._handle_agent_message(event)

        if event.type == EventType.HEARTBEAT_TICK:
            return await self._heartbeat(event)

        if event.type == EventType.SCHEDULED_TASK:
            return await self._handle_scheduled(event)

        return await self._handle_message(event)

    # ── Message handling ──────────────────────

    async def _handle_message(self, event: AgentEvent) -> AgentResponse:
        assert self.memory is not None, "memory required"
        assert self.llm is not None, "llm required"
        session_id = await self.storage.get_or_create_session(event.chat_id, self.name)

        await self.memory.save_message(session_id, "user", event.text, self.name)

        system_prompt = await self._build_system_prompt(event.text)
        _, history = await self.memory.build_context(
            session_id, self.name, task=event.text
        )
        messages = history + [Message(role="user", content=event.text)]

        with log.timer() as t:
            response_text = await self.llm.complete(
                messages=messages,
                system=system_prompt,
            )
        log.info("LLM responded", event="llm_done", duration_ms=t.ms)

        # Only intercept destructive/high-risk actions — autonomous mode
        # lets WRITE_LOW and EXECUTE pass through without approval
        response_text = await self._handle_action_proposal(event.chat_id, response_text)

        await self.memory.save_message(
            session_id, "assistant", response_text, self.name
        )

        # If the response resolved an incident or fixed something, save as solution
        if _looks_like_solution(event.text, response_text):
            topic = _slugify(event.text[:60])
            await self.memory.save_solution(self.name, topic, response_text)
            self.memory.schedule_consolidation(self.name)
            log.info("Solution saved", event="solution_saved", topic=topic)

        return await self.reply(event, response_text)

    # ── Action proposal interception ──────────

    async def _handle_action_proposal(self, chat_id: str, response_text: str) -> str:
        """
        Intercept ACTION: lines. In autonomous mode most actions proceed
        immediately — only DESTRUCTIVE actions require approval.
        """
        assert self.safety is not None, "safety required"
        if "ACTION:" not in response_text:
            return response_text

        lines = response_text.splitlines()
        action_lines = [line for line in lines if line.strip().startswith("ACTION:")]

        for action_line in action_lines:
            parts = action_line.replace("ACTION:", "").strip().split("|", 1)
            action_type_str = parts[0].strip().upper() if parts else ""
            description = parts[1].strip() if len(parts) > 1 else action_line

            action_type = _ACTION_MAP.get(action_type_str, SafetyActionType.WRITE_HIGH)

            allowed = await self.safety.check_action(
                chat_id=chat_id,
                action_type=action_type,
                autonomy_level=self.autonomy_level,
                description=description,
            )

            if not allowed:
                response_text = response_text.replace(
                    action_line,
                    f"⚠️ Action blocked — approval required: _{description}_",
                )
                log.warning(
                    "Destructive action blocked",
                    event="action_blocked",
                    action=action_type_str,
                    description=description,
                )

        return response_text

    # ── Heartbeat ────────────────────────────

    async def _heartbeat(self, event: AgentEvent) -> AgentResponse:
        """
        Periodic health check. Runs every N minutes via scheduler.
        Sends an alert only if something is wrong — silent on green.
        """
        log.info("Heartbeat tick", event="heartbeat")

        checks = await self._run_health_checks()
        failures = [name for name, ok in checks.items() if not ok]

        if failures:
            alert = (
                "🚨 *DevOps Alert*\n\n"
                "Health check failures detected:\n"
                + "\n".join(f"  • {f}" for f in failures)
            )
            sent = await self.notifier.send(
                event.chat_id,
                alert,
                action_type=ActionType.PROACTIVE,
                agent_name=self.name,
            )
            log.warning(
                "Health check failures", event="health_alert", failures=failures
            )
            if not sent:
                await self.notifier.notify_deferred(
                    event.chat_id,
                    alert,
                    self.name,
                )
            return AgentResponse(
                text=alert,
                agent_name=self.name,
                data={"failures": failures},
                deferred=not sent,
            )
            log.warning(
                "Health check failures", event="health_alert", failures=failures
            )
            return AgentResponse(
                text=alert,
                agent_name=self.name,
                data={"failures": failures},
                deferred=not sent,
            )

        log.info("All systems healthy", event="heartbeat_ok")
        return AgentResponse(text="HEARTBEAT_OK", agent_name=self.name)

    async def _run_health_checks(self) -> dict[str, bool]:
        """
        Health check registry — runs real tool checks.
        Returns True (healthy) or False (failing) per system.
        """
        results: dict[str, bool] = {}

        # Storage reachability
        try:
            await self.storage.search_history("_health_", agent=self.name, limit=1)
            results["storage"] = True
        except Exception as e:
            log.error("Storage health check failed", event="check_fail", error=str(e))
            results["storage"] = False

        # LLM reachability
        results["llm"] = self.llm is not None

        # GitHub — check CLI is available and authenticated
        try:
            summary = await self.tools.github.get_health_summary()
            results["github"] = len(summary.get("errors", [])) == 0
        except ToolError as e:
            log.warning("GitHub health check failed", event="check_fail", error=str(e))
            results["github"] = False

        # Railway — check deployment status
        try:
            rw_health = await self.tools.railway.get_health_summary()
            results["railway"] = rw_health.get("healthy", False)
        except ToolError as e:
            log.warning("Railway health check failed", event="check_fail", error=str(e))
            results["railway"] = False

        return results

    # ── Scheduled task handlers ───────────────

    async def _handle_scheduled(self, event: AgentEvent) -> AgentResponse:
        task = event.data.get("task")
        if task == "github_digest":
            return await self._github_digest(event)
        if task == "incident_watchdog":
            return await self._incident_watchdog(event)
        log.warning("Unknown scheduled task", event="unknown_task", task=task)
        return AgentResponse(text="Unknown task.", agent_name=self.name, success=False)

    async def _github_digest(self, event: AgentEvent) -> AgentResponse:
        """Morning GitHub activity digest — pulls real data from gh CLI then summarises."""
        assert self.memory is not None, "memory required"
        assert self.llm is not None, "llm required"
        log.info("Running GitHub digest", event="github_digest")

        # Fetch real data from GitHub
        try:
            gh_summary = await self.tools.github.get_health_summary()
            open_prs = gh_summary.get("open_prs", [])
            failing_ci = gh_summary.get("failing_ci", [])
            gh_errors = gh_summary.get("errors", [])
        except ToolError as e:
            log.error(
                "GitHub fetch failed for digest", event="digest_error", error=str(e)
            )
            sent = await self.notifier.send(
                event.chat_id,
                "\U0001f419 *GitHub Digest*\n\n\u26a0\ufe0f Could not fetch GitHub data: "
                + str(e),
                action_type=ActionType.PROACTIVE,
                agent_name=self.name,
            )
            return AgentResponse(
                text="GitHub fetch failed.",
                agent_name=self.name,
                success=False,
                deferred=not sent,
            )

        NL = "\n"

        pr_text = (
            NL.join(
                f"- [{pr.get('repo')}] #{pr.get('number')} {pr.get('title')} "
                f"(by {pr.get('author', {}).get('login', '?')}, "
                f"review: {pr.get('reviewDecision') or 'pending'})"
                for pr in open_prs
            )
            or "None"
        )

        ci_text = (
            NL.join(
                f"- [{r.get('repo')}] {r.get('name')} \u2014 {r.get('url')}"
                for r in failing_ci
            )
            or "None"
        )

        errors_text = (
            (
                "**Fetch errors:**\n"
                + NL.join(
                    f"- {err.get('repo')}: {err.get('error')}" for err in gh_errors
                )
                + "\n\n"
            )
            if gh_errors
            else ""
        )

        skill_content = ""
        if self.skill_loader:
            skills = await self.skill_loader.find_relevant(
                "github pr review pull request", str(_SKILLS_DIR), max_skills=1
            )
            skill_content = "\n\n".join(skills)

        projects = await self.memory.get_context("projects")
        system = _SYSTEM_TEMPLATE.format(
            context="## Active Projects\n" + projects,
            skills="## Active Skill\n" + skill_content if skill_content else "",
        )

        prompt = (
            "Generate a morning GitHub digest from this live data.\n\n"
            f"**Open PRs ({len(open_prs)}):**\n{pr_text}\n\n"
            f"**Failing CI:**\n{ci_text}\n\n"
            f"{errors_text}"
            "Summarise clearly. Flag anything that needs action today. "
            "Note any PRs open more than 3 days. Be brief — bullets only."
        )

        digest = await self.llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=system,
        )

        sent = await self.notifier.send(
            event.chat_id,
            "\U0001f419 *GitHub Digest*\n\n" + digest,
            action_type=ActionType.PROACTIVE,
            agent_name=self.name,
        )
        log.info(
            "GitHub digest sent",
            event="digest_sent",
            open_prs=len(open_prs),
            failing_ci=len(failing_ci),
        )
        return AgentResponse(text=digest, agent_name=self.name, deferred=not sent)

    async def _incident_watchdog(self, event: AgentEvent) -> AgentResponse:
        """
        Periodic scan for active incidents — checks Railway status and
        failing CI. Silent if all clear; sends alert only if something is wrong.
        """
        log.info("Running incident watchdog", event="incident_watchdog")

        alerts: list[str] = []

        # Check Railway deployment health
        try:
            rw = await self.tools.railway.get_health_summary()
            if not rw.get("healthy"):
                status = rw.get("status", {})
                alerts.append(
                    f"Railway unhealthy — status: {status.get('status', 'unknown')}"
                )
        except ToolError as e:
            alerts.append(f"Railway check failed: {e}")

        # Check for failing CI across all repos
        try:
            gh = await self.tools.github.get_health_summary()
            failing = gh.get("failing_ci", [])
            if failing:
                repos_failing = list({r.get("repo") for r in failing})
                alerts.append(f"Failing CI on: {', '.join(repos_failing)}")
        except ToolError as e:
            alerts.append(f"GitHub check failed: {e}")

        if alerts:
            bullet_list = "\n".join(f"  \u2022 {a}" for a in alerts)
            message = f"\U0001f6a8 *Incident Watchdog*\n\n{bullet_list}"
            sent = await self.notifier.send(
                event.chat_id,
                message,
                action_type=ActionType.PROACTIVE,
                agent_name=self.name,
            )
            log.warning(
                "Watchdog alerts fired", event="watchdog_alert", count=len(alerts)
            )
            if not sent:
                await self.notifier.notify_deferred(
                    event.chat_id,
                    message,
                    self.name,
                )
            return AgentResponse(
                text=message,
                agent_name=self.name,
                data={"alerts": alerts},
                deferred=not sent,
            )
            log.warning(
                "Watchdog alerts fired", event="watchdog_alert", count=len(alerts)
            )
            return AgentResponse(
                text=message,
                agent_name=self.name,
                data={"alerts": alerts},
                deferred=not sent,
            )

        log.info("Watchdog: all clear", event="watchdog_ok")
        return AgentResponse(text="WATCHDOG_OK", agent_name=self.name)

    # ── System prompt builder ─────────────────

    async def _build_system_prompt(self, task: str) -> str:
        assert self.memory is not None, "memory required"
        skill_content = ""
        if self.skill_loader:
            skills = await self.skill_loader.find_relevant(
                task, str(_SKILLS_DIR), max_skills=3
            )
            if skills:
                skill_content = "## Relevant Skills\n\n" + "\n\n---\n\n".join(skills)

        markdown_context, _ = await self.memory.build_context(
            "_unused_", self.name, task=task
        )

        return _SYSTEM_TEMPLATE.format(
            context=f"## Context\n{markdown_context}" if markdown_context else "",
            skills=skill_content,
        )

    # ── Lifecycle ─────────────────────────────

    async def register_schedules(self, bus: "MessageBus") -> None:
        await super().register_schedules(bus)
        try:
            from core.scheduler import scheduler

            primary_chat = (
                self.settings.telegram_allowed_chat_ids[0]
                if self.settings.telegram_allowed_chat_ids
                else ""
            )

            # GitHub digest — weekdays at 9am
            scheduler.add_cron_job(
                cron="0 9 * * 1-5",
                event=AgentEvent(
                    type=EventType.SCHEDULED_TASK,
                    agent_name=self.name,
                    chat_id=primary_chat,
                    data={"task": "github_digest"},
                ),
                bus=bus,
            )

            # Incident watchdog — every 15 minutes
            scheduler.add_cron_job(
                cron="*/15 * * * *",
                event=AgentEvent(
                    type=EventType.SCHEDULED_TASK,
                    agent_name=self.name,
                    chat_id=primary_chat,
                    data={"task": "incident_watchdog"},
                ),
                bus=bus,
            )

            log.info(
                "Schedules registered", event="schedules_registered", agent=self.name
            )

        except (ImportError, AttributeError) as e:
            log.warning(
                "Could not register schedules — check scheduler.py interface",
                event="schedule_error",
                error=str(e),
            )

    async def health_check(self) -> bool:
        import shutil

        try:
            assert self.llm is not None, "LLM not injected"
            assert self.memory is not None, "Memory not injected"
            assert self.safety is not None, "Safety not injected"
            await self.storage.search_history("_health_", agent=self.name, limit=1)

            missing = [cli for cli in ("gh", "railway") if not shutil.which(cli)]
            if missing:
                log.warning(
                    "DevOps agent missing required CLI tools",
                    event="health_cli_missing",
                    missing=missing,
                    hint="Install and authenticate the missing tools before using the DevOps agent.",
                )
                return False

            return True
        except Exception as e:
            log.error("Health check failed", event="health_check_error", error=str(e))
            return False


# ── Helpers ───────────────────────────────────


def _looks_like_solution(question: str, answer: str) -> bool:
    """
    Heuristic: if the question mentions an error/issue and the answer
    is substantial, it's probably worth saving as a solution.
    """
    problem_keywords = {
        "error",
        "fail",
        "broken",
        "crash",
        "fix",
        "debug",
        "issue",
        "problem",
        "incident",
        "down",
        "timeout",
    }
    q_lower = question.lower()
    return any(kw in q_lower for kw in problem_keywords) and len(answer) > 200


def _slugify(text: str) -> str:
    """Convert a string to a safe filename slug."""
    import re

    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")
