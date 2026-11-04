"""
agents/librarian/tools
----------------------
Tool factory for the Librarian Agent, mirroring the devops tools pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents.librarian.tools.graphify import GraphifyTool


@dataclass
class LibrarianTools:
    graphify: GraphifyTool


def build_tools(knowledge_dir: Path) -> LibrarianTools:
    return LibrarianTools(graphify=GraphifyTool(knowledge_dir))
