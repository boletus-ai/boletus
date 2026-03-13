"""Load agent definitions from crew.yaml config."""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration for a single AI agent."""

    name: str
    channel: str
    system_prompt: str
    model: str = "sonnet"
    role: str = "worker"  # leader | manager | worker
    tools: str | None = None
    delegates_to: list[str] = field(default_factory=list)
    reports_to: str | None = None
    receives_context: list[str] = field(default_factory=list)
    integrations: list[str] | None = None


def load_agents(config: dict) -> dict[str, AgentConfig]:
    """Parse agents from config into AgentConfig objects.

    Args:
        config: Parsed crew.yaml configuration dict.

    Returns:
        Dict mapping agent name to AgentConfig.
    """
    agents = {}
    raw_agents = config.get("agents", {})

    for name, raw in raw_agents.items():
        # Default context injection based on role
        role = raw.get("role", "worker")
        default_context = _default_context_for_role(role)
        receives_context = raw.get("receives_context", default_context)

        agent = AgentConfig(
            name=name,
            channel=raw["channel"],
            system_prompt=raw["system_prompt"],
            model=raw.get("model", "sonnet"),
            role=role,
            tools=raw.get("tools"),
            delegates_to=raw.get("delegates_to", []),
            reports_to=raw.get("reports_to"),
            receives_context=receives_context,
            integrations=raw.get("integrations"),
        )
        agents[name] = agent
        logger.info(f"Loaded agent: {name} (role={role}, model={agent.model}, channel=#{agent.channel})")

    return agents


def _default_context_for_role(role: str) -> list[str]:
    """Default context injection based on agent role."""
    if role == "leader":
        return ["business_context", "team_channels", "project_context", "saved_context"]
    if role == "manager":
        return ["team_channels", "project_context"]
    return ["project_context"]


def get_leader(agents: dict[str, AgentConfig]) -> AgentConfig | None:
    """Find the leader agent (runs planning + reports)."""
    for agent in agents.values():
        if agent.role == "leader":
            return agent
    return None


def get_agents_by_role(agents: dict[str, AgentConfig], role: str) -> list[AgentConfig]:
    """Get all agents with a specific role."""
    return [a for a in agents.values() if a.role == role]


def get_delegation_targets(agent: AgentConfig, all_agents: dict[str, AgentConfig]) -> list[AgentConfig]:
    """Get the agents this agent can delegate to."""
    return [all_agents[name] for name in agent.delegates_to if name in all_agents]


def get_effective_channel(agent_name: str, agents: dict[str, AgentConfig]) -> str:
    """Resolve the actual Slack channel an agent should use.

    Workers share their manager's channel. Leaders and managers use their own.
    Walks the ``reports_to`` chain up to find the nearest manager/leader channel.
    """
    agent = agents.get(agent_name)
    if not agent:
        return agent_name  # fallback

    # Leaders and managers own their channel
    if agent.role in ("leader", "manager"):
        return agent.channel

    # Workers: walk up reports_to to find manager's channel
    visited: set[str] = set()
    current = agent
    while current.reports_to and current.reports_to not in visited:
        visited.add(current.name)
        parent = agents.get(current.reports_to)
        if not parent:
            break
        if parent.role in ("leader", "manager"):
            return parent.channel
        current = parent

    # Fallback to agent's own channel field
    return agent.channel
