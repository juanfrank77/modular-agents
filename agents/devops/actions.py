"""
agents/devops/actions.py
--------------------------
ActionSpec registry mapping approved DevOps ACTION: types to real
DevOpsTools calls. Adding a new tool later (e.g. a Neon tool for real
DB_MIGRATE support) means adding the tool to DevOpsTools/build_tools()
and one new ActionSpec entry here — no changes to parsing or the
orchestration flow in agent.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from agents.devops.tools import DevOpsTools


class MissingRequiredArg(Exception):
    """Raised by resolve_args when a required key is missing."""


@dataclass
class ActionSpec:
    required: list[str]
    defaults: dict[str, str]
    schema: dict[str, dict]
    description: str
    describe: Callable[[dict[str, str]], str]
    execute: Callable[["DevOpsTools", dict[str, str]], Awaitable[str]]


def resolve_args(spec: ActionSpec, parsed_args: dict[str, str]) -> dict[str, str]:
    """Merge spec.defaults under parsed_args, then verify all required keys present."""
    resolved = {**spec.defaults, **parsed_args}
    for key in spec.required:
        if key not in resolved or resolved[key] in (None, ""):
            raise MissingRequiredArg(key)
    return resolved


async def _run_merge_pr(tools: "DevOpsTools", args: dict[str, str]) -> str:
    number = int(args["number"])
    repo = args["repo"]
    method = args["method"]
    await tools.github.merge_pr(number=number, repo=repo, method=method)
    return f"✅ Auto-merge enabled for PR #{number} in {repo} ({method})"


async def _run_create_issue(tools: "DevOpsTools", args: dict[str, str]) -> str:
    repo = args["repo"]
    title = args["title"]
    body = args.get("body", "")
    result = await tools.github.create_issue(repo=repo, title=title, body=body)
    return f"✅ Created issue in {repo}: {title} → {result['url']}"


async def _run_deploy(tools: "DevOpsTools", args: dict[str, str]) -> str:
    service = args.get("service") or None
    environment = args["environment"]
    await tools.railway.deploy(service=service, environment=environment)
    label = service or "default service"
    return f"✅ Deploy triggered: {label} → {environment}"


async def _run_rollback(tools: "DevOpsTools", args: dict[str, str]) -> str:
    deployment_id = args["deployment_id"]
    service = args.get("service") or None
    environment = args.get("environment") or None
    await tools.railway.rollback(
        deployment_id=deployment_id, service=service, environment=environment
    )
    label = service or "default service"
    return f"✅ Rolled back {label} to {deployment_id}"


def _label(args: dict[str, str], key: str) -> str:
    return args.get(key) or "default service"


ACTIONS: dict[str, ActionSpec] = {
    "MERGE_PR": ActionSpec(
        required=["number", "repo"],
        defaults={"method": "rebase"},
        schema={
            "number": {"type": "integer", "description": "PR number"},
            "repo": {"type": "string", "description": "owner/repo, e.g. org/x"},
            "method": {"type": "string", "enum": ["merge", "squash", "rebase"]},
        },
        description="Enable auto-merge for a GitHub pull request.",
        describe=lambda a: f"Merge PR #{a['number']} in {a['repo']} ({a['method']})",
        execute=_run_merge_pr,
    ),
    "CREATE_ISSUE": ActionSpec(
        required=["repo", "title"],
        defaults={"body": ""},
        schema={
            "repo": {"type": "string", "description": "owner/repo, e.g. org/x"},
            "title": {"type": "string", "description": "Issue title"},
            "body": {"type": "string", "description": "Issue body"},
        },
        description="Create a GitHub issue.",
        describe=lambda a: f"Create issue in {a['repo']}: {a['title']}",
        execute=_run_create_issue,
    ),
    "DEPLOY_PROD": ActionSpec(
        required=[],
        defaults={"service": "", "environment": "production"},
        schema={
            "service": {"type": "string", "description": "Service name (empty for default service)"},
        },
        description="Deploy a service to production via Railway.",
        describe=lambda a: f"Deploy {_label(a, 'service')} → production",
        execute=_run_deploy,
    ),
    "DEPLOY_STAGING": ActionSpec(
        required=[],
        defaults={"service": "", "environment": "staging"},
        schema={
            "service": {"type": "string", "description": "Service name (empty for default service)"},
        },
        description="Deploy a service to staging via Railway.",
        describe=lambda a: f"Deploy {_label(a, 'service')} → staging",
        execute=_run_deploy,
    ),
    "DB_ROLLBACK": ActionSpec(
        required=["deployment_id"],
        defaults={"service": "", "environment": ""},
        schema={
            "deployment_id": {"type": "string", "description": "Railway deployment ID to roll back to"},
            "service": {"type": "string", "description": "Service name (empty for default service)"},
            "environment": {"type": "string", "description": "Environment name"},
        },
        description="Roll back a Railway deployment.",
        describe=lambda a: f"Roll back {_label(a, 'service')} to {a['deployment_id']}",
        execute=_run_rollback,
    ),
}