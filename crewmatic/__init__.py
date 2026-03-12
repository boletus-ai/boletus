"""Crewmatic — Your first AI company."""

__version__ = "0.1.0"

from .agent_loader import AgentConfig, load_agents, get_leader
from .claude_runner import ClaudeRunner
from .config import load_config
from .task_manager import TaskManager
from .project_manager import ProjectManager
from .delegation import parse_delegations, handle_delegations
from .bot import CrewmaticBot

__all__ = [
    "AgentConfig",
    "ClaudeRunner",
    "CrewmaticBot",
    "load_agents",
    "load_config",
    "get_leader",
    "handle_delegations",
    "parse_delegations",
    "ProjectManager",
    "TaskManager",
]
