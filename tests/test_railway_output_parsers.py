"""
test_railway_output_parsers.py
---------------------------------
Tests for agents.devops.tools.railway's free-text CLI output parsers —
_parse_status_output and _parse_deployments_output. The Railway CLI has
no --json flag for these commands, so this hand-rolled parsing is the
brittle surface improvement-ideas.md §8 flags: if the CLI's output format
shifts, get_health_summary's "healthy" check (used by the hourly
incident_watchdog) could silently misparse and fire forever.

Run:
    python -m pytest tests/test_railway_output_parsers.py -x -q
"""

from __future__ import annotations

from agents.devops.tools.railway import _parse_deployments_output, _parse_status_output


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


class TestParseDeploymentsOutput:
    def test_extracts_id_status_and_timestamp(self):
        text = "abcdef1234567890 SUCCESS 2026-07-19 12:00:00\n"
        deployments = _parse_deployments_output(text)
        assert len(deployments) == 1
        dep = deployments[0]
        assert dep["id"] == "abcdef1234567890"
        assert dep["status"] == "SUCCESS"
        assert dep["created_at"] == "2026-07-19 12:00:00"

    def test_skips_blank_lines(self):
        text = "abcdef1234567890 SUCCESS 2026-07-19 12:00:00\n\n\n"
        deployments = _parse_deployments_output(text)
        assert len(deployments) == 1

    def test_skips_comment_lines(self):
        text = "# header comment\nabcdef1234567890 SUCCESS 2026-07-19 12:00:00\n"
        deployments = _parse_deployments_output(text)
        assert len(deployments) == 1

    def test_multiple_deployments_all_parsed(self):
        text = (
            "abcdef1234567890 SUCCESS 2026-07-19 12:00:00\n"
            "1234567890abcdef CRASHED 2026-07-18 09:30:00\n"
        )
        deployments = _parse_deployments_output(text)
        assert len(deployments) == 2
        assert deployments[0]["status"] == "SUCCESS"
        assert deployments[1]["status"] == "CRASHED"

    def test_raw_line_is_always_preserved(self):
        line = "abcdef1234567890 SUCCESS 2026-07-19 12:00:00"
        deployments = _parse_deployments_output(line + "\n")
        assert deployments[0]["raw"] == line

    def test_line_without_hex_like_first_token_omits_id(self):
        text = "not-an-id SUCCESS 2026-07-19 12:00:00\n"
        deployments = _parse_deployments_output(text)
        assert "id" not in deployments[0]
        assert deployments[0]["status"] == "SUCCESS"

    def test_single_token_line_produces_no_deployment(self):
        text = "onlyonetoken\n"
        deployments = _parse_deployments_output(text)
        assert deployments == []

    def test_empty_text_returns_empty_list(self):
        assert _parse_deployments_output("") == []


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
