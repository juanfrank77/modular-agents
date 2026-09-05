"""
agents/business/actions.py
---------------------------
ActionSpec registry mapping approved Business ACTION: types to real
BusinessTools calls (Gmail/Calendar via Composio).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from agents.business.tools import BusinessTools


class MissingRequiredArg(Exception):
    """Raised by resolve_args when a required key is missing."""


class BusinessToolError(Exception):
    """Raised when a Composio action call returns an {"error": ...} result."""


@dataclass
class ActionSpec:
    required: list[str]
    defaults: dict[str, str]
    schema: dict[str, dict]
    description: str
    describe: Callable[[dict[str, Any]], str]
    execute: Callable[["BusinessTools", dict[str, Any]], Awaitable[str]]


def resolve_args(spec: ActionSpec, parsed_args: dict[str, Any]) -> dict[str, Any]:
    """Merge spec.defaults under parsed_args, then verify all required keys present."""
    resolved = {**spec.defaults, **parsed_args}
    for key in spec.required:
        if key not in resolved or resolved[key] in (None, ""):
            raise MissingRequiredArg(key)
    return resolved


def _check_error(result: dict) -> None:
    if "error" in result:
        raise BusinessToolError(result["error"])


async def _run_send_email(tools: "BusinessTools", args: dict[str, str]) -> str:
    to = args["to"]
    result = await tools.gmail.send_email(to=to, subject=args["subject"], body=args["body"])
    _check_error(result)
    return f"✅ Email sent to {to}"


async def _run_block_time(tools: "BusinessTools", args: dict[str, str]) -> str:
    title = args["title"]
    result = await tools.calendar.block_time(
        title=title,
        start=args["start"],
        end=args["end"],
    )
    _check_error(result)
    return f"✅ Time blocked: {title}"


async def _run_create_event(tools: "BusinessTools", args: dict[str, str]) -> str:
    title = args["title"]
    result = await tools.calendar.create_event(
        title=title,
        start=args["start"],
        end=args["end"],
        description=args.get("description", ""),
    )
    _check_error(result)
    return f"✅ Calendar event created: {title}"


async def _run_draft_reply(tools: "BusinessTools", args: dict[str, str]) -> str:
    email_id = args["email_id"]
    result = await tools.gmail.draft_reply(email_id=email_id, body=args["body"])
    _check_error(result)
    return f"✅ Draft reply created for {email_id}"


async def _run_read_local_file(tools: "BusinessTools", args: dict[str, str]) -> str:
    path = args["path"]
    result = await tools.local_file.read_file(path)

    if "error" in result:
        return f"❌ Could not read {path}: {result['error']}"

    content = result["content"]
    truncated = " (truncated)" if result.get("truncated") else ""
    return f"📄 {result['path']}{truncated}\n\n{content}"


async def _run_write_local_file(tools: "BusinessTools", args: dict[str, str]) -> str:
    path = args["path"]
    result = await tools.local_file.write_file(path, args["content"])

    if "error" in result:
        return f"❌ Could not write {path}: {result['error']}"

    return f"✅ Wrote {result['path']} ({result['bytes_written']} bytes)"


ACTIONS: dict[str, ActionSpec] = {
    "SEND_EMAIL": ActionSpec(
        required=["to", "subject", "body"],
        defaults={},
        schema={
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject line"},
            "body": {"type": "string", "description": "Email body text"},
        },
        description="Send an email via Gmail.",
        describe=lambda a: f"Send email to {a['to']}: {a['subject']}",
        execute=_run_send_email,
    ),
    "CALENDAR_WRITE": ActionSpec(
        required=["title", "start", "end"],
        defaults={"description": ""},
        schema={
            "title": {"type": "string", "description": "Event title"},
            "start": {"type": "string", "description": "ISO 8601 start datetime"},
            "end": {"type": "string", "description": "ISO 8601 end datetime"},
            "description": {"type": "string", "description": "Event description"},
        },
        description="Create a calendar event.",
        describe=lambda a: f"Create calendar event '{a['title']}' ({a['start']} → {a['end']})",
        execute=_run_create_event,
    ),
    "BLOCK_TIME": ActionSpec(
        required=["title", "start", "end"],
        defaults={},
        schema={
            "title": {"type": "string", "description": "Description of the time block"},
            "start": {"type": "string", "description": "ISO 8601 start datetime"},
            "end": {"type": "string", "description": "ISO 8601 end datetime"},
        },
        description="Block off time on the calendar.",
        describe=lambda a: f"Block time for '{a['title']}' ({a['start']} → {a['end']})",
        execute=_run_block_time,
    ),
    "DRAFT": ActionSpec(
        required=["email_id", "body"],
        defaults={},
        schema={
            "email_id": {"type": "string", "description": "ID of the email to reply to"},
            "body": {"type": "string", "description": "Draft reply body text"},
        },
        description="Create a draft reply to an email.",
        describe=lambda a: f"Draft reply to message {a['email_id']}",
        execute=_run_draft_reply,
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