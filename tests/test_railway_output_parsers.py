"""
test_railway_output_parsers.py
---------------------------------
Tests for agents.devops.tools.railway's free-text CLI output parser —
_parse_status_output. The Railway CLI has no --json flag for these
commands, so this hand-rolled parsing is the brittle surface
improvement-ideas.md §8 flags: if the CLI's output format shifts,
get_health_summary's "healthy" check (used by the hourly
incident_watchdog) could silently misparse and fire forever.

Run:
    python -m pytest tests/test_railway_output_parsers.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.devops.tools.railway import _parse_status_output


class TestParseStatusOutput:
    def test_extracts_status_line(self):
        text = "Status: ACTIVE\n"
        result = _parse_status_output(text)
        assert result["status"] == "ACTIVE"

    def test_extracts_all_known_fields(self):
        text = (
            "Status: SUCCESS\n"
            "Deployed: 2026-07-19T12:00:00Z\n"
            "URL: https://api.example.com\n"
            "Build: #42\n"
        )
        result = _parse_status_output(text)
        assert result["status"] == "SUCCESS"
        assert result["deployed_at"] == "2026-07-19T12:00:00Z"
        assert result["url"] == "https://api.example.com"
        assert result["build"] == "#42"

    def test_field_matching_is_case_insensitive_on_label(self):
        text = "status: deployed\n"
        result = _parse_status_output(text)
        assert result["status"] == "deployed"

    def test_preserves_service_and_environment_args(self):
        result = _parse_status_output("Status: ACTIVE\n", service="api", environment="production")
        assert result["service"] == "api"
        assert result["environment"] == "production"

    def test_raw_text_is_preserved_verbatim(self):
        text = "Status: ACTIVE\nsome extra noise\n"
        result = _parse_status_output(text)
        assert result["raw"] == text

    def test_lines_without_colon_are_ignored(self):
        text = "Status ACTIVE (no colon)\n"
        result = _parse_status_output(text)
        assert "status" not in result

    def test_unrecognized_lines_are_ignored(self):
        text = "Some: unrelated field\n"
        result = _parse_status_output(text)
        assert result == {"service": "", "environment": "", "raw": text}

    def test_empty_text_returns_only_base_fields(self):
        result = _parse_status_output("")
        assert result == {"service": "", "environment": "", "raw": ""}

    def test_last_matching_line_wins_when_field_appears_twice(self):
        text = "Status: ACTIVE\nStatus: CRASHED\n"
        result = _parse_status_output(text)
        assert result["status"] == "CRASHED"


class TestParseStatusOutputHealthCheckContract:
    """get_health_summary() treats status in {ACTIVE, SUCCESS, DEPLOYED} as
    healthy — pin the exact strings _parse_status_output must produce for
    that check to keep working."""

    def test_active_status_parses_to_exact_healthy_string(self):
        assert _parse_status_output("Status: ACTIVE\n")["status"] == "ACTIVE"

    def test_success_status_parses_to_exact_healthy_string(self):
        assert _parse_status_output("Status: SUCCESS\n")["status"] == "SUCCESS"

    def test_deployed_status_parses_to_exact_healthy_string(self):
        assert _parse_status_output("Status: DEPLOYED\n")["status"] == "DEPLOYED"

    def test_crashed_status_is_not_in_healthy_set(self):
        status = _parse_status_output("Status: CRASHED\n")["status"]
        assert status not in ("ACTIVE", "SUCCESS", "DEPLOYED")


class TestGetHealthSummaryParseFailure:
    """get_health_summary() must distinguish 'the CLI output changed shape
    and we can't tell what's going on' from 'we parsed it fine and it's
    actually down' — the former should be diagnosable, not silent."""

    @pytest.mark.asyncio
    async def test_unparseable_output_is_flagged_distinctly(self, monkeypatch):
        from agents.devops.tools.railway import RailwayTool

        tool = RailwayTool(memory=MagicMock())
        monkeypatch.setattr(
            tool, "get_status",
            AsyncMock(return_value={"service": "", "environment": "", "raw": "some new format\nnothing recognizable\n"}),
        )

        result = await tool.get_health_summary()

        assert result["healthy"] is False
        assert result["parse_error"] is True

    @pytest.mark.asyncio
    async def test_recognized_healthy_status_has_no_parse_error(self, monkeypatch):
        from agents.devops.tools.railway import RailwayTool

        tool = RailwayTool(memory=MagicMock())
        monkeypatch.setattr(
            tool, "get_status",
            AsyncMock(return_value={"service": "", "environment": "", "status": "ACTIVE", "raw": "Status: ACTIVE\n"}),
        )

        result = await tool.get_health_summary()

        assert result["healthy"] is True
        assert "parse_error" not in result

    @pytest.mark.asyncio
    async def test_recognized_unhealthy_status_has_no_parse_error(self, monkeypatch):
        from agents.devops.tools.railway import RailwayTool

        tool = RailwayTool(memory=MagicMock())
        monkeypatch.setattr(
            tool, "get_status",
            AsyncMock(return_value={"service": "", "environment": "", "status": "CRASHED", "raw": "Status: CRASHED\n"}),
        )

        result = await tool.get_health_summary()

        assert result["healthy"] is False
        assert "parse_error" not in result
