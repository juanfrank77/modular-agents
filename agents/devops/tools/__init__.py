from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents.devops.tools.github import GitHubTool
from agents.devops.tools.railway import RailwayTool

if TYPE_CHECKING:
    from core.protocols import MemoryStore


@dataclass
class DevOpsTools:
    github: GitHubTool
    railway: RailwayTool


def build_tools(memory: "MemoryStore") -> DevOpsTools:
    return DevOpsTools(
        github=GitHubTool(memory=memory),
        railway=RailwayTool(memory=memory),
    )
