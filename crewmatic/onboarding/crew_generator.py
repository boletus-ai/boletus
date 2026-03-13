"""YAML generation and validation for crew configurations."""

import logging
import os
from pathlib import Path
from typing import Callable

import yaml

from .prompts import ADD_AGENT_PROMPT, CREW_GENERATION_PROMPT

logger = logging.getLogger(__name__)

# Type alias for the LLM callable used throughout this module.
LLMCallFn = Callable[[str, str], str]


def generate_crew_yaml(
    llm_call_fn: LLMCallFn,
    business_desc: str,
    tech_details: str,
    integrations: list[str] | None = None,
) -> tuple[str, dict]:
    """Generate a complete crew.yaml from business context.

    Args:
        llm_call_fn: ``(system_prompt, user_message) -> response_text``.
        business_desc: Free-form business description from the user.
        tech_details: Follow-up answers about tech stack, roles, etc.
        integrations: Optional list of integration names the user selected.

    Returns:
        A tuple of ``(raw_yaml_string, parsed_config_dict)``.
        On validation failure after retry the dict will contain an
        ``"_error"`` key with the error message.
    """
    from ..integrations import list_integrations

    available = ", ".join(sorted(i["key"] for i in list_integrations())) or "none"
    selected = ", ".join(integrations) if integrations else "none"

    prompt = CREW_GENERATION_PROMPT.format(
        business_description=business_desc,
        tech_details=tech_details,
        available_integrations=available,
        selected_integrations=selected,
    )

    raw_yaml = llm_call_fn(
        "You are a DevOps expert that generates crew.yaml configurations. "
        "Output ONLY valid YAML. No markdown, no commentary.",
        prompt,
    )

    raw_yaml = _strip_yaml_fences(raw_yaml)

    parsed, error = _parse_and_validate(raw_yaml)
    if error:
        logger.warning(f"First generation failed validation: {error}")
        # Retry once — feed the error back to the LLM
        retry_prompt = (
            f"The YAML you generated has a validation error:\n{error}\n\n"
            f"Here is the YAML you produced:\n{raw_yaml}\n\n"
            "Fix the error and output ONLY the corrected YAML. No fences, no explanation."
        )
        raw_yaml = llm_call_fn(
            "You are a DevOps expert. Fix the YAML validation error. "
            "Output ONLY valid YAML.",
            retry_prompt,
        )
        raw_yaml = _strip_yaml_fences(raw_yaml)
        parsed, error = _parse_and_validate(raw_yaml)
        if error:
            logger.error(f"Retry also failed validation: {error}")
            return raw_yaml, {"_error": error}

    return raw_yaml, parsed


def generate_agent_yaml(
    llm_call_fn: LLMCallFn,
    request: str,
    existing_agents: dict,
) -> tuple[str, dict]:
    """Generate a single new agent definition.

    Args:
        llm_call_fn: ``(system_prompt, user_message) -> response_text``.
        request: What the user wants the new agent to do.
        existing_agents: The current ``agents:`` section from crew.yaml.

    Returns:
        A tuple of ``(raw_yaml_block, parsed_agent_dict)``.
        On failure the dict will contain an ``"_error"`` key.
    """
    existing_yaml = yaml.dump(existing_agents, default_flow_style=False)
    prompt = ADD_AGENT_PROMPT.format(
        request=request,
        existing_agents_yaml=existing_yaml,
    )

    raw_yaml = llm_call_fn(
        "You are a DevOps expert. Generate a single agent YAML block. "
        "Output ONLY valid YAML.",
        prompt,
    )
    raw_yaml = _strip_yaml_fences(raw_yaml)

    try:
        parsed = yaml.safe_load(raw_yaml)
        if not isinstance(parsed, dict) or len(parsed) == 0:
            return raw_yaml, {"_error": "Expected a YAML mapping with one agent definition."}
        return raw_yaml, parsed
    except yaml.YAMLError as exc:
        logger.error(f"Failed to parse agent YAML: {exc}")
        return raw_yaml, {"_error": f"Invalid YAML: {exc}"}


def save_crew_yaml(config_dir: str, yaml_content: str) -> str:
    """Write crew.yaml to disk.

    Args:
        config_dir: Directory where crew.yaml should live.
        yaml_content: Raw YAML string to write.

    Returns:
        Absolute path to the written file.
    """
    config_dir_path = Path(config_dir)
    config_dir_path.mkdir(parents=True, exist_ok=True)

    # Also create standard subdirectories
    for subdir in ("data", "memory", "context"):
        (config_dir_path / subdir).mkdir(exist_ok=True)

    crew_path = config_dir_path / "crew.yaml"
    crew_path.write_text(yaml_content, encoding="utf-8")
    logger.info(f"Saved crew.yaml to {crew_path}")
    return str(crew_path.resolve())


def merge_agent_into_config(config_path: str, agent_name: str, agent_config: dict) -> str:
    """Add a new agent to an existing crew.yaml.

    Args:
        config_path: Path to the existing crew.yaml.
        agent_name: Name key for the new agent.
        agent_config: Agent configuration dict (channel, model, etc.).

    Returns:
        The path to the updated crew.yaml.

    Raises:
        FileNotFoundError: If config_path does not exist.
        ValueError: If the agent name already exists.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError(f"Invalid crew.yaml: expected a mapping, got {type(config)}")

    agents = config.setdefault("agents", {})
    if agent_name in agents:
        raise ValueError(f"Agent '{agent_name}' already exists in crew.yaml")

    agents[agent_name] = agent_config

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info(f"Merged agent '{agent_name}' into {path}")
    return str(path.resolve())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_yaml_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wrapped its output."""
    text = text.strip()
    if text.startswith("```yaml"):
        text = text[len("```yaml"):]
    elif text.startswith("```yml"):
        text = text[len("```yml"):]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _parse_and_validate(raw_yaml: str) -> tuple[dict | None, str | None]:
    """Parse YAML and run config validation.

    Returns:
        ``(parsed_dict, None)`` on success, or ``(None, error_message)`` on failure.
    """
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        return None, f"Invalid YAML syntax: {exc}"

    if not isinstance(parsed, dict):
        return None, f"Expected a YAML mapping, got {type(parsed).__name__}"

    # Reuse the existing config._validate logic
    try:
        from ..config import _validate
        _validate(parsed)
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"Unexpected validation error: {exc}"

    return parsed, None
