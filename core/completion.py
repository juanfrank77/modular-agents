"""
core/completion.py
------------------
Validated completion — evidence-checked "done" for task-shaped work.

Scope day one: DevOps PR/issue existence via the `gh` CLI. When a DevOps
action claims to have merged a PR or created an issue, the verifier spot-
checks the claimed identifier against GitHub before the report is accepted.
Verification failure bounces the report back with an error so the agent can
retry or report failure honestly; success logs the evidence.

This is intentionally small and concrete. Add new verifiers here only after
the PR/issue pattern proves out — do not build a generic framework on day one.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from core.logger import get_logger

log = get_logger("completion")


@dataclass
class VerificationResult:
    """Outcome of a single completion verification."""

    ok: bool
    evidence: str = ""
    error: str = ""


class CompletionVerifier:
    """Verify claimed completion artifacts against their source of truth."""

    async def verify_pr_exists(self, repo: str, number: int) -> VerificationResult:
        """
        Check that a GitHub pull request exists in `repo`.

        Returns ok=True with the PR URL as evidence when found.
        Returns ok=False with the CLI error when missing or unreachable.
        """
        returncode, _stdout, stderr = await self._run_gh(
            ["gh", "pr", "view", str(number), "--repo", repo, "--json", "number,url"]
        )
        return self._parse_result(returncode, stderr, "PR", repo, number)

    async def verify_issue_exists(self, repo: str, url: str) -> VerificationResult:
        """
        Check that a GitHub issue exists in `repo`.

        `url` is parsed for the issue number (e.g. https://github.com/org/repo/issues/42).
        Returns ok=True with the issue URL as evidence when found.
        Returns ok=False when the URL is unparseable or the issue is missing.
        """
        number = self._extract_issue_number(url)
        if number is None:
            return VerificationResult(
                ok=False,
                error=f"Could not parse issue number from URL: {url}",
            )

        returncode, _stdout, stderr = await self._run_gh(
            ["gh", "issue", "view", str(number), "--repo", repo, "--json", "number,url"]
        )
        return self._parse_result(returncode, stderr, "issue", repo, number)

    async def _run_gh(self, args: list[str]) -> tuple[int, str, str]:
        """Run a gh CLI command and return (returncode, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return (
                127,
                "",
                "'gh' CLI not found on PATH. Install GitHub CLI and authenticate.",
            )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=15
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return (
                124,
                "",
                "'gh' CLI timed out after 15s. Check network/auth and retry.",
            )
        return (
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace").strip(),
            stderr_b.decode("utf-8", errors="replace").strip(),
        )

    def _parse_result(
        self,
        returncode: int,
        stderr: str,
        kind: str,
        repo: str,
        number: int,
    ) -> VerificationResult:
        if returncode == 0:
            return VerificationResult(
                ok=True,
                evidence=f"{kind} #{number} in {repo}",
            )

        error = stderr or f"{kind} #{number} not found in {repo}"
        return VerificationResult(ok=False, error=error)

    _ISSUE_URL_RE = re.compile(r"/issues/(\d+)")

    def _extract_issue_number(self, url: str) -> int | None:
        match = self._ISSUE_URL_RE.search(url)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None
