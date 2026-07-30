"""
tests/test_github_error_e2e.py
-------------------------------
E2E-style tests for GitHubTool's error handling (issue #38).

`list_prs()` and `list_runs()` swallow `ToolError` per-repo and append an
error-shaped entry (e.g. `{"repo": r, "error": str(e)}`) to their result
list instead of raising. `get_health_summary()` then aggregates both lists.
It already filters error entries out of `failing_ci`, but was NOT filtering
them out of `open_prs` — so a single failing repo would leak a malformed
dict into `open_prs`, which the morning-digest formatter renders as a
garbled PR row like `#None None (by ?, review: pending)` and which also
inflates the "Open PRs (N)" count.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.devops.tools.cli_runner import CLIResult, ToolError
from agents.devops.tools.github import GitHubTool


class _FakeMemory:
    """Minimal MemoryStore stub providing two repos via projects.md content."""

    async def get_context(self, key: str) -> str:
        assert key == "projects"
        return "repo: org/good-repo\nrepo: org/bad-repo\n"


def _format_pr_row(pr: dict) -> str:
    """
    Mirrors the PR row formatting used by agents/devops/agent.py's
    `_github_digest` (see agent.py around line 524-526), so we can verify
    what the digest would actually render for a given `open_prs` entry.
    """
    return (
        f"- [{pr.get('repo')}] #{pr.get('number')} {pr.get('title')} "
        f"(by {pr.get('author', {}).get('login', '?')}, "
        f"review: {pr.get('reviewDecision') or 'pending'})"
    )


@pytest.fixture
def gh() -> GitHubTool:
    return GitHubTool(memory=_FakeMemory())


def _cli_side_effect(good_stdout: str):
    """Return an async run_cli replacement: succeeds for good-repo, raises
    ToolError for bad-repo, based on the `--repo` argument."""

    async def _run_cli(args, tool_name="", **kwargs):
        repo = args[args.index("--repo") + 1]
        if repo == "org/bad-repo":
            raise ToolError(tool_name, args, stderr="boom", returncode=1)
        return CLIResult(stdout=good_stdout, stderr="", returncode=0)

    return _run_cli


@pytest.mark.asyncio
async def test_list_prs_appends_error_entry_instead_of_raising(gh: GitHubTool):
    """Documents current (intentional) behavior: list_prs never raises;
    a failing repo becomes an error-shaped dict in the result list."""
    good_stdout = '[{"number": 1, "title": "Fix bug", "author": {"login": "alice"}, ' \
                  '"state": "OPEN", "isDraft": false, "reviewDecision": null, ' \
                  '"createdAt": "2026-07-01T00:00:00Z", "url": "https://x", "headRefName": "b"}]'
    with patch(
        "agents.devops.tools.github.run_cli",
        side_effect=_cli_side_effect(good_stdout),
    ):
        prs = await gh.list_prs()

    assert len(prs) == 2
    error_entries = [p for p in prs if p.get("error")]
    assert len(error_entries) == 1
    assert error_entries[0] == {"repo": "org/bad-repo", "error": error_entries[0]["error"]}
    assert "boom" in error_entries[0]["error"]


@pytest.mark.asyncio
async def test_list_runs_appends_error_entry_instead_of_raising(gh: GitHubTool):
    """Same behavior as list_prs, but for list_runs (failing_ci source)."""
    good_stdout = '[{"databaseId": 1, "name": "CI", "status": "completed", ' \
                  '"conclusion": "failure", "createdAt": "2026-07-01T00:00:00Z", ' \
                  '"url": "https://x", "headBranch": "main"}]'
    with patch(
        "agents.devops.tools.github.run_cli",
        side_effect=_cli_side_effect(good_stdout),
    ):
        runs = await gh.list_runs()

    assert len(runs) == 2
    error_entries = [r for r in runs if r.get("error")]
    assert len(error_entries) == 1
    assert error_entries[0]["repo"] == "org/bad-repo"


@pytest.mark.asyncio
async def test_get_health_summary_never_includes_error_entries_in_open_prs(gh: GitHubTool):
    """
    Regression test for issue #38.

    get_health_summary() already filters error-shaped entries out of
    failing_ci (`[r for r in runs if not r.get("error")]`); it must apply
    the same filter to open_prs before extending, so a failing repo's
    error dict never ends up sitting in open_prs alongside real PRs.
    """
    good_pr_stdout = '[{"number": 7, "title": "Add feature", ' \
                      '"author": {"login": "bob"}, "state": "OPEN", "isDraft": false, ' \
                      '"reviewDecision": "APPROVED", "createdAt": "2026-07-01T00:00:00Z", ' \
                      '"url": "https://x", "headRefName": "b"}]'
    good_run_stdout = "[]"

    async def _run_cli(args, tool_name="", **kwargs):
        repo = args[args.index("--repo") + 1]
        if repo == "org/bad-repo":
            raise ToolError(tool_name, args, stderr="boom", returncode=1)
        if "pr" in args:
            return CLIResult(stdout=good_pr_stdout, stderr="", returncode=0)
        return CLIResult(stdout=good_run_stdout, stderr="", returncode=0)

    with patch("agents.devops.tools.github.run_cli", side_effect=_run_cli):
        summary = await gh.get_health_summary()

    open_prs = summary["open_prs"]

    # The real PR from org/good-repo must be present.
    assert any(p.get("number") == 7 for p in open_prs)

    # No error-shaped entry should ever leak into open_prs.
    assert all(not p.get("error") for p in open_prs), (
        f"error-shaped entry leaked into open_prs: {open_prs}"
    )

    # And, as a consequence, the digest formatter must never render a
    # malformed row (e.g. '#None None (by ?, review: pending)') for it.
    rendered = [_format_pr_row(pr) for pr in open_prs]
    assert not any("#None None" in row for row in rendered), rendered


@pytest.mark.asyncio
async def test_digest_formatter_produces_malformed_output_for_error_entries(gh: GitHubTool):
    """
    NOTE: prior to the fix for issue #38, this test asserted the buggy
    behavior as if it were correct — i.e. it fed the *unfiltered* output
    of list_prs() (including a raw `{"repo": r, "error": ...}` dict)
    straight into the digest row formatter and asserted the malformed
    "#None None (by ?, review: pending)" row was produced. That merely
    documented the bug rather than testing correct behavior.

    Now that get_health_summary() filters error entries out of open_prs,
    this test instead asserts the fixed, end-to-end behavior: feeding
    get_health_summary()'s open_prs into the digest formatter never
    produces a malformed row, even when one repo failed to fetch.
    """
    good_pr_stdout = '[{"number": 42, "title": "Improve docs", ' \
                      '"author": {"login": "carol"}, "state": "OPEN", "isDraft": false, ' \
                      '"reviewDecision": null, "createdAt": "2026-07-01T00:00:00Z", ' \
                      '"url": "https://x", "headRefName": "b"}]'

    async def _run_cli(args, tool_name="", **kwargs):
        repo = args[args.index("--repo") + 1]
        if repo == "org/bad-repo":
            raise ToolError(tool_name, args, stderr="boom", returncode=1)
        if "pr" in args:
            return CLIResult(stdout=good_pr_stdout, stderr="", returncode=0)
        return CLIResult(stdout="[]", stderr="", returncode=0)

    with patch("agents.devops.tools.github.run_cli", side_effect=_run_cli):
        summary = await gh.get_health_summary()

    open_prs = summary["open_prs"]
    rendered_rows = [_format_pr_row(pr) for pr in open_prs]

    assert len(open_prs) == 1
    assert rendered_rows == [
        "- [org/good-repo] #42 Improve docs (by carol, review: pending)"
    ]
    assert not any("#None None" in row for row in rendered_rows)
