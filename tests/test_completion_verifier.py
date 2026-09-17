"""
test_completion_verifier.py
---------------------------
Tests for core/completion.CompletionVerifier.

Run:
    python3 -m pytest tests/test_completion_verifier.py -x -q
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.completion import CompletionVerifier, VerificationResult


def _mock_process(returncode: int = 0, stderr: str = "") -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr.encode("utf-8")))
    return proc


@pytest.mark.asyncio
class TestVerifyPrExists:
    async def test_pr_exists_returns_ok(self):
        verifier = CompletionVerifier()
        with patch(
            "core.completion.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_mock_process(returncode=0)),
        ) as mock_exec:
            result = await verifier.verify_pr_exists("org/repo", 42)

        assert result == VerificationResult(ok=True, evidence="PR #42 in org/repo")
        mock_exec.assert_awaited_once()
        args = mock_exec.call_args[0]
        assert args[:4] == ("gh", "pr", "view", "42")
        assert args[4:6] == ("--repo", "org/repo")

    async def test_pr_missing_returns_error(self):
        verifier = CompletionVerifier()
        with patch(
            "core.completion.asyncio.create_subprocess_exec",
            new=AsyncMock(
                return_value=_mock_process(
                    returncode=1, stderr="GraphQL: Could not resolve to a PullRequest"
                )
            ),
        ):
            result = await verifier.verify_pr_exists("org/repo", 42)

        assert result.ok is False
        assert "PullRequest" in result.error

    async def test_gh_cli_missing_returns_error(self):
        verifier = CompletionVerifier()
        with patch(
            "core.completion.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("gh")),
        ):
            result = await verifier.verify_pr_exists("org/repo", 42)

        assert result.ok is False
        assert "not found on PATH" in result.error


@pytest.mark.asyncio
class TestVerifyIssueExists:
    async def test_issue_url_parsed_and_exists(self):
        verifier = CompletionVerifier()
        with patch(
            "core.completion.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_mock_process(returncode=0)),
        ) as mock_exec:
            result = await verifier.verify_issue_exists(
                "org/repo", "https://github.com/org/repo/issues/7"
            )

        assert result == VerificationResult(ok=True, evidence="issue #7 in org/repo")
        mock_exec.assert_awaited_once()
        args = mock_exec.call_args[0]
        assert args[:4] == ("gh", "issue", "view", "7")

    async def test_unparseable_url_returns_error(self):
        verifier = CompletionVerifier()
        result = await verifier.verify_issue_exists("org/repo", "not a url")

        assert result.ok is False
        assert "Could not parse issue number" in result.error

    async def test_issue_missing_returns_error(self):
        verifier = CompletionVerifier()
        with patch(
            "core.completion.asyncio.create_subprocess_exec",
            new=AsyncMock(
                return_value=_mock_process(
                    returncode=1, stderr="GraphQL: Could not resolve to an Issue"
                )
            ),
        ):
            result = await verifier.verify_issue_exists(
                "org/repo", "https://github.com/org/repo/issues/99"
            )

        assert result.ok is False
        assert "Could not resolve to an Issue" in result.error

    async def test_gh_timeout_treated_as_verification_failure(self):
        verifier = CompletionVerifier()
        proc = AsyncMock()
        proc.returncode = 0
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=0)

        async def _communicate():
            raise asyncio.TimeoutError()

        proc.communicate = _communicate

        with patch(
            "core.completion.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await verifier.verify_pr_exists("org/repo", 42)

        assert result.ok is False
        assert "timed out" in result.error
        proc.kill.assert_called_once()
