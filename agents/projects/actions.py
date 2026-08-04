"""
agents/projects/actions.py
--------------------------
ActionSpec registry mapping approved Projects ACTION: types to real
ProjectsTools calls (web search and local file access).
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
}
