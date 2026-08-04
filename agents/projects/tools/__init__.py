from __future__ import annotations

from dataclasses import dataclass

from core.config import Settings
from core.local_file_tool import LocalFileTool
from core.web_tool import WebTool


@dataclass
class ProjectsTools:
    web: WebTool
    local_file: LocalFileTool


def build_tools(settings: Settings) -> ProjectsTools:
    return ProjectsTools(
        web=WebTool(search_api_key=settings.tavily_api_key),
        local_file=LocalFileTool(allowed_paths=settings.local_file_paths),
    )
