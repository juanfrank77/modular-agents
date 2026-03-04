"""
agents/devops/tools/github.py
------------------------------
GitHub tool — wraps the `gh` CLI for multi-repo operations.

Repos are sourced from memory/context/projects.md at call time,
so the agent always works against the current project list without
needing a restart.

All functions return plain dicts/lists — the agent formats them
for display. Nothing here sends Telegram messages.

Requires: gh CLI authenticated (`gh auth login`)

Usage:
    from agents.devops.tools.github import GitHubTool
    gh = GitHubTool(memory=memory)
    prs = await gh.list_prs()
    issues = await gh.list_issues(repo="org/repo", state="open")
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from agents.devops.tools.cli_runner import ToolError, run_cli
from core.logger import get_logger

if TYPE_CHECKING:
    from core.memory import Memory

log = get_logger("devops.github")

# Fields requested from gh's --json flag
_PR_FIELDS = "number,title,author,state,isDraft,reviewDecision,createdAt,url,headRefName"
_ISSUE_FIELDS = "number,title,author,state,labels,createdAt,url,assignees"
_RUN_FIELDS = "databaseId,name,status,conclusion,createdAt,url,headBranch"


class GitHubTool:
    def __init__(self, memory: "Memory") -> None:
        self._memory = memory

    # ── Repo resolution ───────────────────────

    async def get_repos(self) -> list[str]:
        """
        Parse repo slugs (org/name) from projects.md.
        Looks for lines matching patterns like:
          - github: org/repo
          - repo: org/repo
          - https://github.com/org/repo
        """
        content = await self._memory.get_context("projects")
        repos: list[str] = []

        for line in content.splitlines():
            # Match explicit repo: or github: keys
            m = re.search(r"(?:repo|github)\s*:\s*([\w.-]+/[\w.-]+)", line, re.I)
            if m:
                repos.append(m.group(1).strip())
                continue
            # Match github.com URLs
            m = re.search(r"github\.com/([\w.-]+/[\w.-]+)", line)
            if m:
                slug = m.group(1).rstrip("/")
                repos.append(slug)

        seen: set[str] = set()
        unique = [r for r in repos if not (r in seen or seen.add(r))]  # type: ignore[func-returns-value]

        if not unique:
            log.warning("No repos found in projects.md", event="no_repos")
        else:
            log.info("Resolved repos", event="repos_resolved", repos=unique)

        return unique

    # ── Pull Requests ─────────────────────────

    async def list_prs(
        self,
        repo: str | None = None,
        state: str = "open",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        List pull requests across all project repos, or a specific one.
        state: "open" | "closed" | "merged" | "all"
        """
        repos = [repo] if repo else await self.get_repos()
        all_prs: list[dict[str, Any]] = []

        for r in repos:
            try:
                result = await run_cli(
                    ["gh", "pr", "list",
                     "--repo", r,
                     "--state", state,
                     "--limit", str(limit),
                     "--json", _PR_FIELDS],
                    tool_name="github",
                )
                prs = json.loads(result.stdout or "[]")
                for pr in prs:
                    pr["repo"] = r
                all_prs.extend(prs)
            except ToolError as e:
                log.error("Failed to list PRs", event="pr_list_error",
                          repo=r, error=str(e))
                all_prs.append({"repo": r, "error": str(e)})

        return all_prs

    async def get_pr(self, number: int, repo: str) -> dict[str, Any]:
        """Get full details of a single PR including review status."""
        result = await run_cli(
            ["gh", "pr", "view", str(number),
             "--repo", repo,
             "--json", _PR_FIELDS + ",body,comments,reviews"],
            tool_name="github",
        )
        pr = json.loads(result.stdout)
        pr["repo"] = repo
        return pr

    async def get_pr_diff(self, number: int, repo: str) -> str:
        """Return the raw diff for a PR."""
        result = await run_cli(
            ["gh", "pr", "diff", str(number), "--repo", repo],
            tool_name="github",
        )
        return result.stdout

    async def merge_pr(self, number: int, repo: str, method: str = "squash") -> dict[str, Any]:
        """
        Merge a PR. method: "merge" | "squash" | "rebase"
        Note: agent must have gotten approval before calling this.
        """
        result = await run_cli(
            ["gh", "pr", "merge", str(number),
             "--repo", repo,
             f"--{method}",
             "--auto"],
            tool_name="github",
        )
        log.info("PR merged", event="pr_merged", repo=repo, number=number, method=method)
        return {"repo": repo, "number": number, "merged": True, "output": result.stdout}

    # ── Issues ────────────────────────────────

    async def list_issues(
        self,
        repo: str | None = None,
        state: str = "open",
        label: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List issues across all project repos, or a specific one."""
        repos = [repo] if repo else await self.get_repos()
        all_issues: list[dict[str, Any]] = []

        for r in repos:
            try:
                args = ["gh", "issue", "list",
                        "--repo", r,
                        "--state", state,
                        "--limit", str(limit),
                        "--json", _ISSUE_FIELDS]
                if label:
                    args += ["--label", label]

                result = await run_cli(args, tool_name="github")
                issues = json.loads(result.stdout or "[]")
                for issue in issues:
                    issue["repo"] = r
                all_issues.extend(issues)
            except ToolError as e:
                log.error("Failed to list issues", event="issue_list_error",
                          repo=r, error=str(e))
                all_issues.append({"repo": r, "error": str(e)})

        return all_issues

    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new issue."""
        args = ["gh", "issue", "create",
                "--repo", repo,
                "--title", title,
                "--body", body or ""]
        if labels:
            for label in labels:
                args += ["--label", label]

        result = await run_cli(args, tool_name="github")
        log.info("Issue created", event="issue_created", repo=repo, title=title)
        return {"repo": repo, "title": title, "url": result.stdout.strip()}

    # ── CI / Actions ──────────────────────────

    async def list_runs(
        self,
        repo: str | None = None,
        limit: int = 10,
        branch: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List recent GitHub Actions workflow runs.
        status: "completed" | "in_progress" | "queued" | "failure" | "success"
        """
        repos = [repo] if repo else await self.get_repos()
        all_runs: list[dict[str, Any]] = []

        for r in repos:
            try:
                args = ["gh", "run", "list",
                        "--repo", r,
                        "--limit", str(limit),
                        "--json", _RUN_FIELDS]
                if branch:
                    args += ["--branch", branch]
                if status:
                    args += ["--status", status]

                result = await run_cli(args, tool_name="github")
                runs = json.loads(result.stdout or "[]")
                for run in runs:
                    run["repo"] = r
                all_runs.extend(runs)
            except ToolError as e:
                log.error("Failed to list runs", event="run_list_error",
                          repo=r, error=str(e))
                all_runs.append({"repo": r, "error": str(e)})

        return all_runs

    async def get_run_logs(self, run_id: int, repo: str) -> str:
        """Fetch logs for a specific workflow run."""
        result = await run_cli(
            ["gh", "run", "view", str(run_id),
             "--repo", repo,
             "--log-failed"],
            tool_name="github",
        )
        return result.stdout

    async def rerun_failed(self, run_id: int, repo: str) -> dict[str, Any]:
        """Re-run only the failed jobs in a workflow run."""
        result = await run_cli(
            ["gh", "run", "rerun", str(run_id),
             "--repo", repo,
             "--failed"],
            tool_name="github",
        )
        log.info("Run restarted", event="run_rerun", repo=repo, run_id=run_id)
        return {"repo": repo, "run_id": run_id, "output": result.stdout}

    # ── Summary helpers ───────────────────────

    async def get_health_summary(self) -> dict[str, Any]:
        """
        Quick multi-repo health snapshot: failing CI, stale PRs, open incidents.
        Used by the heartbeat and morning digest.
        """
        repos = await self.get_repos()
        summary: dict[str, Any] = {
            "repos_checked": repos,
            "failing_ci": [],
            "stale_prs": [],      # open > 3 days, no activity
            "open_prs": [],
            "errors": [],
        }

        for repo in repos:
            try:
                # Failing CI runs
                runs = await self.list_runs(repo=repo, limit=5, status="failure")
                summary["failing_ci"].extend(
                    {"repo": repo, "name": r.get("name"), "url": r.get("url")}
                    for r in runs if not r.get("error")
                )

                # Open PRs
                prs = await self.list_prs(repo=repo, state="open")
                summary["open_prs"].extend(prs)

            except ToolError as e:
                summary["errors"].append({"repo": repo, "error": str(e)})

        return summary