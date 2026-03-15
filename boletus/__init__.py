"""Boletus — Your first AI company."""

__version__ = "0.1.0"

# Lightweight imports (no external dependencies)
from .agent_loader import AgentConfig, load_agents, get_leader
from .config import load_config
from .delegation import parse_delegations, handle_delegations
from .task_manager import TaskManager
from .project_manager import ProjectManager


def __getattr__(name):
    """Lazy imports for modules with heavy dependencies (slack, dotenv)."""
    if name == "BoletusBot":
        from .bot import BoletusBot
        return BoletusBot
    if name == "ClaudeRunner":
        from .claude_runner import ClaudeRunner
        return ClaudeRunner
    if name == "WorkflowEngine":
        from .workflows import WorkflowEngine
        return WorkflowEngine
    raise AttributeError(f"module 'boletus' has no attribute {name!r}")


__all__ = [
    "AgentConfig",
    "ClaudeRunner",
    "BoletusBot",
    "load_agents",
    "load_config",
    "get_leader",
    "handle_delegations",
    "parse_delegations",
    "ProjectManager",
    "TaskManager",
    "WorkflowEngine",
]
