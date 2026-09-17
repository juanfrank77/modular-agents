"""
agents/projects/actions.py
--------------------------
ActionSpec registry mapping approved Projects ACTION: types to real
ProjectsTools calls (web search, local file read/write).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from agents.projects.tools import ProjectsTools


class MissingRequiredArg(Exception):
    """Raised by resolve_args when a required key is missing."""


@dataclass
class ActionSpec:
    required: list[str]
    defaults: dict[str, str]
    schema: dict[str, dict]
    description: str
    describe: Callable[[dict[str, Any]], str]
    execute: Callable[["ProjectsTools", dict[str, Any]], Awaitable[str]]


def resolve_args(spec: ActionSpec, parsed_args: dict[str, Any]) -> dict[str, Any]:
    """Merge spec.defaults under parsed_args, then verify all required keys present."""
    resolved = {**spec.defaults, **parsed_args}
    for key in spec.required:
        if key not in resolved or resolved[key] in (None, ""):
            raise MissingRequiredArg(key)
    return resolved


async def _run_web_search(tools: "ProjectsTools", args: dict[str, str]) -> str:
    query = args["query"]
    max_results = int(args.get("max_results", "5"))
    results = await tools.web.search(query, max_results=max_results)

    if not results:
        return "No web results found."

    lines = [f"Web results for: {query}"]
    for result in results:
        title = result.get("title", "Untitled")
        url = result.get("url", "")
        content = result.get("content", "")
        lines.append(f"- {title}\n  {url}\n  {content[:500]}")
    return "\n\n".join(lines)


async def _run_read_local_file(tools: "ProjectsTools", args: dict[str, str]) -> str:
    path = args["path"]
    result = await tools.local_file.read_file(path)

    if "error" in result:
        return f"❌ Could not read {path}: {result['error']}"

    content = result["content"]
    truncated = " (truncated)" if result.get("truncated") else ""
    return f"📄 {result['path']}{truncated}\n\n{content}"


async def _run_write_local_file(tools: "ProjectsTools", args: dict[str, str]) -> str:
    path = args["path"]
    result = await tools.local_file.write_file(path, args["content"])

    if "error" in result:
        return f"❌ Could not write {path}: {result['error']}"

    return f"✅ Wrote {result['path']} ({result['bytes_written']} bytes)"


async def _run_ask_user(_tools: "ProjectsTools", _args: dict[str, str]) -> str:
    """Placeholder — ASK_USER is handled directly by the agent."""
    raise RuntimeError("ASK_USER must be handled by the agent")


ACTIONS: dict[str, ActionSpec] = {
    "WEB_SEARCH": ActionSpec(
        required=["query"],
        defaults={"max_results": "5"},
        schema={
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Maximum number of results"},
        },
        description="Search the web using Tavily.",
        describe=lambda a: f"Web search: {a['query']}",
        execute=_run_web_search,
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
    "ASK_USER": ActionSpec(
        required=["question", "question_type"],
        defaults={"choices": "", "default": ""},
        schema={
            "question": {"type": "string", "description": "The clarification question to ask the user"},
            "question_type": {
                "type": "string",
                "enum": ["text", "choice", "confirm"],
                "description": "Question type: confirm (yes/no), choice (pick one), or text (returns default)",
            },
            "choices": {"type": "string", "description": "Comma-separated choices when question_type=choice"},
            "default": {"type": "string", "description": "Default answer if the user does not respond in time"},
        },
        description=(
            "Ask the user a clarifying question mid-task. "
            "Use confirm for yes/no, choice for a list of options, text when the default is acceptable."
        ),
        describe=lambda a: f"Ask user: {a['question']}",
        execute=_run_ask_user,
    ),
}
