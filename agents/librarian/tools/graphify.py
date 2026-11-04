"""
agents/librarian/tools/graphify.py
----------------------------------
Thin wrapper around the `graphify` CLI (https://github.com/Graphify-Labs/graphify).
Maintains a knowledge graph over the librarian's notes directory and answers
questions by graph traversal instead of plain keyword matching.

The graph lives at <knowledge_dir>/graphify-out/graph.json.
`graphify update <path>` re-extracts changed files without needing an LLM,
so it's cheap enough to run after every ingested resource.

Everything degrades gracefully: if the CLI is not installed or the graph
doesn't exist yet, callers get None/False and fall back to keyword search.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agents.devops.tools.cli_runner import ToolError, run_cli
from core.logger import get_logger

log = get_logger("librarian.graphify")

_UPDATE_TIMEOUT = 300.0  # first extraction over a large folder can be slow
_QUERY_TIMEOUT = 60.0
_QUERY_BUDGET = 1200  # token cap for query answers fed into the LLM prompt


class GraphifyTool:
    def __init__(self, knowledge_dir: Path) -> None:
        self._knowledge_dir = knowledge_dir
        self._graph_path = knowledge_dir / "graphify-out" / "graph.json"

    def available(self) -> bool:
        """True if the graphify CLI is on PATH."""
        return shutil.which("graphify") is not None

    def has_graph(self) -> bool:
        return self._graph_path.exists()

    async def update(self) -> bool:
        """(Re)build the knowledge graph from the notes directory.
        Returns True on success, False on any failure (logged, never raises)."""
        if not self.available():
            return False
        try:
            await run_cli(
                ["graphify", "update", str(self._knowledge_dir)],
                tool_name="graphify",
                timeout=_UPDATE_TIMEOUT,
                retries=0,
            )
            log.info("Knowledge graph updated", event="graph_updated")
            return True
        except ToolError as e:
            log.warning(
                "Knowledge graph update failed",
                event="graph_update_error",
                error=str(e)[:300],
            )
            return False

    async def query(self, question: str, budget: int = _QUERY_BUDGET) -> str | None:
        """Answer a question by graph traversal.
        Returns the answer text, or None if unavailable/failed."""
        if not self.available() or not self.has_graph():
            return None
        try:
            result = await run_cli(
                [
                    "graphify", "query", question,
                    "--budget", str(budget),
                    "--graph", str(self._graph_path),
                ],
                tool_name="graphify",
                timeout=_QUERY_TIMEOUT,
                retries=0,
            )
            return result.stdout or None
        except ToolError as e:
            log.warning(
                "Knowledge graph query failed",
                event="graph_query_error",
                error=str(e)[:300],
            )
            return None
