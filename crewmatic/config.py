"""Configuration loading from crew.yaml."""

import os
import re
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "max_concurrent_agents": 4,
    "worker_poll_interval": 60,
    "planning_interval": 1800,
    "planning_cooldown": 600,
    "planning_threshold": 3,
    "report_hours": [9, 16, 22],
    "cache_ttl": 300,
    "claude_timeout": 600,
    "slack_max_length": 39000,
    "loop_cooldown": 30,
    "stuck_timeout_minutes": 10,
    "archive_after_days": 30,
    "llm_backend": "cli",
}

ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} placeholders with environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        env_val = os.environ.get(var_name, "")
        if not env_val:
            logger.warning(f"Environment variable ${{{var_name}}} not set")
        return env_val
    return ENV_VAR_PATTERN.sub(replacer, value)


def _interpolate_recursive(obj):
    """Recursively interpolate env vars in strings within dicts/lists."""
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _interpolate_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_recursive(item) for item in obj]
    return obj


def find_config(start_dir: str | None = None) -> Path | None:
    """Find crew.yaml by walking up from start_dir (or cwd)."""
    current = Path(start_dir) if start_dir else Path.cwd()
    while True:
        candidate = current / "crew.yaml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_config(config_path: str | None = None) -> dict:
    """Load and validate crew.yaml configuration.

    Args:
        config_path: Explicit path to crew.yaml. If None, searches from cwd upward.

    Returns:
        Parsed and validated configuration dict.

    Raises:
        FileNotFoundError: If no crew.yaml found.
        ValueError: If configuration is invalid.
    """
    if config_path:
        path = Path(config_path)
    else:
        path = find_config()

    if not path or not path.exists():
        raise FileNotFoundError(
            "No crew.yaml found. Run 'crewmatic init' to create one."
        )

    logger.info(f"Loading config from {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw or not isinstance(raw, dict):
        raise ValueError(f"Invalid crew.yaml: expected a YAML mapping, got {type(raw)}")

    config = _interpolate_recursive(raw)

    # Apply default settings
    settings = {**DEFAULT_SETTINGS, **(config.get("settings") or {})}
    config["settings"] = settings

    # Resolve relative paths against config file location
    config_dir = path.parent
    for key in ("data_dir", "memory_dir", "context_dir"):
        val = config.get(key, f"./{key.replace('_dir', '')}")
        resolved = (config_dir / val).resolve()
        config[key] = str(resolved)

    # Store config directory for reference
    config["_config_dir"] = str(config_dir)
    config["_config_path"] = str(path)

    _validate(config)

    return config


def _validate(config: dict):
    """Validate required fields and agent references."""
    if "name" not in config:
        raise ValueError("crew.yaml must have a 'name' field")

    if "agents" not in config or not config["agents"]:
        raise ValueError("crew.yaml must define at least one agent")

    agents = config["agents"]
    valid_roles = {"leader", "manager", "worker"}
    leader_count = 0

    for name, agent in agents.items():
        if "system_prompt" not in agent:
            raise ValueError(f"Agent '{name}' missing 'system_prompt'")
        if "channel" not in agent:
            raise ValueError(f"Agent '{name}' missing 'channel'")

        role = agent.get("role", "worker")
        if role not in valid_roles:
            raise ValueError(f"Agent '{name}' has invalid role '{role}'. Must be: {valid_roles}")
        if role == "leader":
            leader_count += 1

        # Validate delegation references
        for target in agent.get("delegates_to", []):
            if target not in agents:
                raise ValueError(f"Agent '{name}' delegates to unknown agent '{target}'")

        reports_to = agent.get("reports_to")
        if reports_to and reports_to not in agents:
            raise ValueError(f"Agent '{name}' reports to unknown agent '{reports_to}'")

    if leader_count == 0:
        logger.warning("No agent has role 'leader'. Planning and report loops won't run.")
    if leader_count > 1:
        logger.warning(f"Multiple leaders defined ({leader_count}). Only the first will run planning loops.")
