"""
agents/devops/tools/railway.py
-------------------------------
Railway tool — wraps the `railway` CLI for deployment operations.

Supports: status, deploy, logs, rollback, and environment variable queries.
Services and environments are resolved from projects.md where possible,
but can also be passed directly for ad-hoc use.

Requires: railway CLI authenticated (`railway login`)

Usage:
    from agents.devops.tools.railway import RailwayTool
    rw = RailwayTool(memory=memory)

    status = await rw.get_status()
    await rw.deploy(service="api", environment="production")
    logs = await rw.get_logs(service="api", lines=50)
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from agents.devops.tools.cli_runner import ToolError, run_cli
from core.logger import get_logger

if TYPE_CHECKING:
    from core.memory import Memory

log = get_logger("devops.railway")


class RailwayTool:
    def __init__(self, memory: "Memory") -> None:
        self._memory = memory

    # ── Project / service resolution ──────────

    async def get_project_config(self) -> dict[str, str]:
        """
        Parse Railway project/service/environment from projects.md.
        Looks for lines like:
          - railway-project: my-project
          - railway-service: api
          - railway-environment: production
        """
        content = await self._memory.get_context("projects")
        config: dict[str, str] = {}

        patterns = {
            "project":     r"railway[-\s]project\s*:\s*(\S+)",
            "service":     r"railway[-\s]service\s*:\s*(\S+)",
            "environment": r"railway[-\s]env(?:ironment)?\s*:\s*(\S+)",
        }

        for key, pattern in patterns.items():
            m = re.search(pattern, content, re.I)
            if m:
                config[key] = m.group(1).strip()

        if not config:
            log.warning("No Railway config found in projects.md", event="no_railway_config")
        else:
            log.info("Resolved Railway config", event="railway_config", config=config)

        return config

    # ── Status ────────────────────────────────

    async def get_status(
        self,
        service: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        """
        Get the current deployment status for a service.
        Falls back to projects.md config if service/environment not provided.
        """
        cfg = await self.get_project_config()
        svc = service or cfg.get("service", "")
        env = environment or cfg.get("environment", "production")

        args = ["railway", "status"]
        if svc:
            args += ["--service", svc]
        if env:
            args += ["--environment", env]

        result = await run_cli(args, tool_name="railway")

        # Parse plain-text output into structured form
        return _parse_status_output(result.stdout, service=svc, environment=env)

    # ── Deploy ────────────────────────────────

    async def deploy(
        self,
        service: str | None = None,
        environment: str | None = None,
        detach: bool = True,
    ) -> dict[str, Any]:
        """
        Trigger a deployment.
        Note: agent must have obtained approval before calling this.

        detach=True returns immediately after triggering (don't block waiting).
        detach=False waits for the deploy to complete (can take minutes).
        """
        cfg = await self.get_project_config()
        svc = service or cfg.get("service", "")
        env = environment or cfg.get("environment", "production")

        args = ["railway", "up"]
        if svc:
            args += ["--service", svc]
        if env:
            args += ["--environment", env]
        if detach:
            args += ["--detach"]

        result = await run_cli(args, tool_name="railway", timeout=120.0)

        log.info(
            "Deploy triggered",
            event="deploy_triggered",
            service=svc,
            environment=env,
        )

        return {
            "service": svc,
            "environment": env,
            "triggered": True,
            "detached": detach,
            "output": result.stdout,
        }

    # ── Logs ─────────────────────────────────

    async def get_logs(
        self,
        service: str | None = None,
        environment: str | None = None,
        lines: int = 100,
    ) -> str:
        """
        Fetch recent logs for a service.
        Returns raw log text — the agent summarises or filters as needed.
        """
        cfg = await self.get_project_config()
        svc = service or cfg.get("service", "")
        env = environment or cfg.get("environment", "production")

        args = ["railway", "logs",
                "--lines", str(lines)]
        if svc:
            args += ["--service", svc]
        if env:
            args += ["--environment", env]

        result = await run_cli(args, tool_name="railway", timeout=60.0)
        return result.stdout

    async def get_error_logs(
        self,
        service: str | None = None,
        environment: str | None = None,
        lines: int = 50,
    ) -> str:
        """
        Fetch logs filtered to error/exception lines only.
        Useful for incident triage without the noise of healthy log output.
        """
        raw_logs = await self.get_logs(service=service, environment=environment,
                                       lines=lines * 5)
        error_keywords = {"error", "exception", "traceback", "fatal", "critical",
                          "failed", "panic", "500", "unhandled"}
        error_lines = [
            line for line in raw_logs.splitlines()
            if any(kw in line.lower() for kw in error_keywords)
        ]
        return "\n".join(error_lines[-lines:])  # most recent N error lines

    # ── Rollback ─────────────────────────────

    async def list_deployments(
        self,
        service: str | None = None,
        environment: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List recent deployments — used to pick a rollback target."""
        cfg = await self.get_project_config()
        svc = service or cfg.get("service", "")
        env = environment or cfg.get("environment", "production")

        args = ["railway", "deployments",
                "--limit", str(limit)]
        if svc:
            args += ["--service", svc]
        if env:
            args += ["--environment", env]

        try:
            result = await run_cli(args, tool_name="railway")
            return _parse_deployments_output(result.stdout)
        except ToolError as e:
            log.error("Failed to list deployments", event="deployments_error", error=str(e))
            return [{"error": str(e)}]

    async def rollback(
        self,
        deployment_id: str,
        service: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        """
        Roll back to a specific deployment ID.
        Note: agent must have obtained approval before calling this.
        """
        cfg = await self.get_project_config()
        svc = service or cfg.get("service", "")
        env = environment or cfg.get("environment", "production")

        args = ["railway", "rollback", deployment_id]
        if svc:
            args += ["--service", svc]
        if env:
            args += ["--environment", env]

        result = await run_cli(args, tool_name="railway", timeout=120.0)

        log.info(
            "Rollback triggered",
            event="rollback_triggered",
            deployment_id=deployment_id,
            service=svc,
            environment=env,
        )

        return {
            "deployment_id": deployment_id,
            "service": svc,
            "environment": env,
            "rolled_back": True,
            "output": result.stdout,
        }

    # ── Environment variables ─────────────────

    async def list_env_vars(
        self,
        service: str | None = None,
        environment: str | None = None,
    ) -> dict[str, str]:
        """
        List environment variable keys for a service.
        Returns keys only — never values. Values stay in Railway.
        """
        cfg = await self.get_project_config()
        svc = service or cfg.get("service", "")
        env = environment or cfg.get("environment", "production")

        args = ["railway", "variables"]
        if svc:
            args += ["--service", svc]
        if env:
            args += ["--environment", env]

        result = await run_cli(args, tool_name="railway")

        # Parse KEY=VALUE lines — return only keys for safety
        keys: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key = line.split("=", 1)[0].strip()
                keys[key] = "[set]"  # never expose values

        return keys

    # ── Health summary ────────────────────────

    async def get_health_summary(self) -> dict[str, Any]:
        """
        Quick deployment health snapshot for heartbeat/digest use.
        """
        try:
            status = await self.get_status()
            return {
                "healthy": status.get("status") in ("ACTIVE", "SUCCESS", "DEPLOYED"),
                "status": status,
            }
        except ToolError as e:
            return {"healthy": False, "error": str(e)}


# ── Output parsers ────────────────────────────

def _parse_status_output(text: str, service: str = "", environment: str = "") -> dict[str, Any]:
    """
    Parse `railway status` plain-text output into a dict.
    Railway CLI output is not JSON, so we parse key lines.
    """
    result: dict[str, Any] = {
        "service": service,
        "environment": environment,
        "raw": text,
    }

    for line in text.splitlines():
        line = line.strip()
        lower = line.lower()

        if "status" in lower and ":" in line:
            result["status"] = line.split(":", 1)[1].strip()
        elif "deployed" in lower and ":" in line:
            result["deployed_at"] = line.split(":", 1)[1].strip()
        elif "url" in lower and ":" in line:
            result["url"] = line.split(":", 1)[1].strip()
        elif "build" in lower and ":" in line:
            result["build"] = line.split(":", 1)[1].strip()

    return result


def _parse_deployments_output(text: str) -> list[dict[str, Any]]:
    """
    Parse `railway deployments` output into a list of deployment dicts.
    Each deployment typically has an ID, status, and timestamp.
    """
    deployments = []

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Try to extract: ID, status, timestamp from space-separated columns
        parts = line.split()
        if len(parts) >= 2:
            deployment: dict[str, Any] = {"raw": line}
            # Heuristic: first token that looks like an ID (hex-ish or UUID-ish)
            if re.match(r"^[a-f0-9-]{8,}", parts[0], re.I):
                deployment["id"] = parts[0]
            if len(parts) >= 3:
                deployment["status"] = parts[1]
                deployment["created_at"] = " ".join(parts[2:4])
            deployments.append(deployment)

    return deployments