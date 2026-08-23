from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents.devops.tools.github import GitHubTool
from agents.devops.tools.railway import RailwayTool
from core.config import Settings
from core.local_file_tool import LocalFileTool

if TYPE_CHECKING:
    from core.protocols import MemoryStore


@dataclass
class DevOpsTools:
    github: GitHubTool
    railway: RailwayTool
    local_file: LocalFileTool


def build_tools(settings: Settings, memory: "MemoryStore") -> DevOpsTools:
    return DevOpsTools(
        github=GitHubTool(memory=memory),
        railway=RailwayTool(memory=memory),
        local_file=LocalFileTool(allowed_paths=settings.local_file_paths),
    )
