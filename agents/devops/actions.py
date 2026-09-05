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
from typing import TYPE_CHECKING, Any, Awaitable, Callable

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
    describe: Callable[[dict[str, Any]], str]
    execute: Callable[["DevOpsTools", dict[str, Any]], Awaitable[str]]


def resolve_args(spec: ActionSpec, parsed_args: dict[str, Any]) -> dict[str, Any]:
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


async def _run_list_issues(tools: "DevOpsTools", args: dict[str, str]) -> str:
    repo = args.get("repo") or None
    state = args.get("state", "open")
    label = args.get("label") or None
    limit = int(args.get("limit", "20"))
    issues = await tools.github.list_issues(repo=repo, state=state, label=label, limit=limit)

    if repo:
        header = f"Issues in {repo}"
    else:
        header = "Issues across project repos"
    if state != "open":
        header += f" (state: {state})"
    if label:
        header += f" [label: {label}]"

    lines = [header]
    for issue in issues:
        if issue.get("error"):
            lines.append(f"- [{issue.get('repo')}] error: {issue.get('error')}")
            continue
        lines.append(
            f"- [{issue.get('repo')}] #{issue.get('number')} {issue.get('title')} "
            f"({issue.get('state')}, {issue.get('url')})"
        )
    if len(lines) == 1:
        lines.append("No issues found.")
    return "\n".join(lines)


async def _run_get_status(tools: "DevOpsTools", args: dict[str, str]) -> str:
    service = args.get("service") or None
    environment = args.get("environment") or None
    result = await tools.railway.get_status(service=service, environment=environment)
    status = result.get("status", "unknown")
    return f"✅ Railway status: {status} ({result.get('service', '')} / {result.get('environment', '')})"


async def _run_get_logs(tools: "DevOpsTools", args: dict[str, str]) -> str:
    service = args.get("service") or None
    environment = args.get("environment") or None
    lines = int(args.get("lines", "100"))
    return await tools.railway.get_logs(service=service, environment=environment, lines=lines)


async def _run_get_error_logs(tools: "DevOpsTools", args: dict[str, str]) -> str:
    service = args.get("service") or None
    environment = args.get("environment") or None
    lines = int(args.get("lines", "50"))
    return await tools.railway.get_error_logs(service=service, environment=environment, lines=lines)


async def _run_list_deployments(tools: "DevOpsTools", args: dict[str, str]) -> str:
    service = args.get("service") or None
    environment = args.get("environment") or None
    limit = int(args.get("limit", "10"))
    result = await tools.railway.list_deployments(service=service, environment=environment, limit=limit)
    if result and isinstance(result, list) and result[0].get("error"):
        return f"❌ Could not list deployments: {result[0]['error']}"
    lines = [f"Recent deployments (limit {limit}):"]
    for dep in result:
        lines.append(f"- {dep.get('id', 'unknown')}: {dep.get('status', 'unknown')} ({dep.get('created_at', 'unknown')})")
    return "\n".join(lines)


async def _run_list_env_vars(tools: "DevOpsTools", args: dict[str, str]) -> str:
    service = args.get("service") or None
    environment = args.get("environment") or None
    result = await tools.railway.list_env_vars(service=service, environment=environment)
    keys = list(result.keys())
    return f"✅ Environment variables ({len(keys)} keys): {', '.join(keys)}"


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


async def _run_read_local_file(tools: "DevOpsTools", args: dict[str, str]) -> str:
    path = args["path"]
    result = await tools.local_file.read_file(path)

    if "error" in result:
        return f"❌ Could not read {path}: {result['error']}"

    content = result["content"]
    truncated = " (truncated)" if result.get("truncated") else ""
    return f"📄 {result['path']}{truncated}\n\n{content}"


async def _run_write_local_file(tools: "DevOpsTools", args: dict[str, str]) -> str:
    path = args["path"]
    result = await tools.local_file.write_file(path, args["content"])

    if "error" in result:
        return f"❌ Could not write {path}: {result['error']}"

    return f"✅ Wrote {result['path']} ({result['bytes_written']} bytes)"


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
    "LIST_ISSUES": ActionSpec(
        required=[],
        defaults={"repo": "", "state": "open", "label": "", "limit": "20"},
        schema={
            "repo": {"type": "string", "description": "owner/repo, e.g. org/x (omit for all project repos)"},
            "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Issue state filter"},
            "label": {"type": "string", "description": "Filter by label"},
            "limit": {"type": "integer", "description": "Maximum issues to return per repo"},
        },
        description="List GitHub issues across project repos or a specific repo.",
        describe=lambda a: f"List {a.get('state', 'open')} issues in {a.get('repo') or 'all project repos'}"
        + (f" with label {a.get('label')}" if a.get("label") else ""),
        execute=_run_list_issues,
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
    "GET_STATUS": ActionSpec(
        required=[],
        defaults={"service": "", "environment": ""},
        schema={
            "service": {"type": "string", "description": "Service name (empty for default service)"},
            "environment": {"type": "string", "description": "Environment name"},
        },
        description="Get Railway deployment status for a service.",
        describe=lambda a: f"Get Railway status for {_label(a, 'service')} ({a.get('environment') or 'default environment'})",
        execute=_run_get_status,
    ),
    "FETCH_LOGS": ActionSpec(
        required=[],
        defaults={"service": "", "environment": "", "lines": "100"},
        schema={
            "service": {"type": "string", "description": "Service name (empty for default service)"},
            "environment": {"type": "string", "description": "Environment name"},
            "lines": {"type": "integer", "description": "Number of log lines to fetch"},
        },
        description="Fetch recent logs for a Railway service.",
        describe=lambda a: f"Fetch {a.get('lines', '100')} logs for {_label(a, 'service')} ({a.get('environment') or 'default environment'})",
        execute=_run_get_logs,
    ),
    "FETCH_ERROR_LOGS": ActionSpec(
        required=[],
        defaults={"service": "", "environment": "", "lines": "50"},
        schema={
            "service": {"type": "string", "description": "Service name (empty for default service)"},
            "environment": {"type": "string", "description": "Environment name"},
            "lines": {"type": "integer", "description": "Number of error log lines to fetch"},
        },
        description="Fetch error/exception logs for a Railway service.",
        describe=lambda a: f"Fetch {a.get('lines', '50')} error logs for {_label(a, 'service')} ({a.get('environment') or 'default environment'})",
        execute=_run_get_error_logs,
    ),
    "LIST_DEPLOYMENTS": ActionSpec(
        required=[],
        defaults={"service": "", "environment": "", "limit": "10"},
        schema={
            "service": {"type": "string", "description": "Service name (empty for default service)"},
            "environment": {"type": "string", "description": "Environment name"},
            "limit": {"type": "integer", "description": "Maximum number of deployments to list"},
        },
        description="List recent Railway deployments for a service.",
        describe=lambda a: f"List deployments for {_label(a, 'service')} ({a.get('environment') or 'default environment'})",
        execute=_run_list_deployments,
    ),
    "LIST_ENV_VARS": ActionSpec(
        required=[],
        defaults={"service": "", "environment": ""},
        schema={
            "service": {"type": "string", "description": "Service name (empty for default service)"},
            "environment": {"type": "string", "description": "Environment name"},
        },
        description="List environment variable keys for a Railway service.",
        describe=lambda a: f"List env vars for {_label(a, 'service')} ({a.get('environment') or 'default environment'})",
        execute=_run_list_env_vars,
    ),
    "READ_LOCAL_FILE": ActionSpec(
        required=["path"],
        defaults={},
        schema={
            "path": {"type": "string", "description": "Path to file under a configured local_file_paths root"},
        },
        description="Read a text file from a configured local directory.",
        describe=lambda a: f"Read local file {a['path']}",
        execute=_run_read_local_file,
    ),
    "WRITE_LOCAL_FILE": ActionSpec(
        required=["path", "content"],
        defaults={},
        schema={
            "path": {"type": "string", "description": "Path to file under a configured local_file_paths root"},
            "content": {"type": "string", "description": "Text content to write"},
        },
        description="Write a text file to a configured local directory.",
        describe=lambda a: f"Write local file {a['path']}",
        execute=_run_write_local_file,
    ),
}