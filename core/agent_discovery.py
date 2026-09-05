"""
core/agent_discovery.py
----------------------
Auto-discovers agent modules by scanning agents/*/agent.py for BaseAgent subclasses.

Replaces manual registration in main.py. After creating a new agent via /newagent,
the system finds it automatically on restart without patching main.py.

Usage:
    from core.agent_discovery import discover_agents
    from core.bus import MessageBus
    
    bus = MessageBus()
    agents, failed = discover_agents(settings=settings, bus=bus, **common_kwargs)
    for agent in agents:
        bus.register(agent)
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.config import Settings
    from core.bus import MessageBus

log = get_logger("agent_discovery")

_AGENTS_DIR = Path(__file__).parent.parent / "agents"


def _find_agent_modules() -> list[Path]:
    """Return paths to all agent.py files under agents/"""
    if not _AGENTS_DIR.exists():
        return []
    return sorted(_AGENTS_DIR.glob("*/agent.py"))


def _extract_agent_class_names(agent_path: Path) -> list[str]:
    """Extract all class names that inherit from BaseAgent using AST parsing."""
    try:
        content = agent_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except Exception as e:
        log.warning("Could not parse agent file", event="parse_error", path=str(agent_path), error=str(e))
        return []

    class_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = None
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                
                if base_name == "BaseAgent":
                    class_names.append(node.name)
    
    return class_names


def _load_agent_class(module_path: Path, class_name: str):
    """Dynamically import and return the agent class."""
    import sys
    
    module_name = f"agents.{module_path.parent.name}.agent"
    parent_package = f"agents.{module_path.parent.name}"
    
    # Avoid re-importing if already loaded
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            log.warning("Could not load agent module", event="load_error", agent_module=module_name, error=str(e))
            return None
    
    # exec_module does not bind the submodule onto its parent package the way
    # the normal import machinery does. Import the parent package (if not
    # already loaded) and bind the submodule so that attribute lookup — and
    # mock.patch() — can resolve `agents.<name>.agent` after discovery.
    if parent_package not in sys.modules:
        importlib.import_module(parent_package)
    parent = sys.modules[parent_package]
    submodule_attr = module_path.stem
    if getattr(parent, submodule_attr, None) is not module:
        setattr(parent, submodule_attr, module)
    
    return getattr(module, class_name, None)


async def discover_agents(
    settings: "Settings",
    bus: "MessageBus",
    storage,
    notifier,
    llm,
    memory,
    safety,
    skill_loader,
    state_store=None,
) -> tuple[list, list[tuple[str, str]]]:
    """
    Discover and instantiate all agents found under agents/*.
    
    Agents with routable=False (like EchoAgent) are only instantiated if
    DEBUG_ECHO_AGENT is True. This prevents the echo agent from being
    available in production builds while keeping it useful for dev.
    
    ``state_store`` is used to persist each agent's profile. When it is
    ``None`` (the default) agents are still instantiated and their
    profiles are seeded in memory, but nothing is persisted to disk.
    
    Returns:
        (agents, failed) where agents is a list of instantiated BaseAgent subclasses
        and failed is a list of (module_name, error_message) tuples for any agents
        that failed to load.
    """
    from agents.base import BaseAgent
    
    discovered_agents = []
    failed = []
    failed_agents: set[str] = set()
    
    agent_modules = _find_agent_modules()
    log.info("Scanning for agent modules", event="discovery_scan", count=len(agent_modules))
    
    for agent_path in agent_modules:
        module_name = agent_path.parent.name
        
        class_names = _extract_agent_class_names(agent_path)
        if not class_names:
            log.warning("No BaseAgent subclass found", event="discovery_skip", agent_module=module_name)
            continue
        
        for class_name in class_names:
            agent_class = _load_agent_class(agent_path, class_name)
            if agent_class is None or not issubclass(agent_type := agent_class, BaseAgent):
                failed.append((module_name, f"Class {class_name} not found or not a BaseAgent"))
                continue
            
            # Check routable attribute - skip non-routable agents in production
            is_routable = getattr(agent_class, "routable", True)
            if not is_routable and not settings.debug_echo_agent:
                log.info("Skipping non-routable agent", event="discovery_skip", agent=module_name)
                continue
            
            try:
                instance = agent_type(
                    settings=settings,
                    storage=storage,
                    notifier=notifier,
                    llm=llm,
                    memory=memory,
                    safety=safety,
                    skill_loader=skill_loader,
                    bus=bus,
                    state_store=state_store,
                )
                discovered_agents.append(instance)
                log.info("Agent discovered and instantiated", event="discovery_success", agent=instance.name)
                try:
                    await instance.ensure_profile()
                except Exception as e:
                    log.error(
                        "Failed to ensure agent profile",
                        event="profile_error",
                        agent=instance.name,
                        error=str(e),
                    )
            except Exception as e:
                log.error("Failed to instantiate agent", event="discovery_error", agent=module_name, error=str(e))
                failed_agents.add(module_name)
                failed.append((module_name, str(e)))
    
    return discovered_agents, failed