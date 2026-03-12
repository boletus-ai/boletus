"""Delegation parsing — extract @agent: task patterns from LLM responses."""

import logging
import re

logger = logging.getLogger(__name__)


def parse_delegations(response: str, agent_names: set[str]) -> list[tuple[str, str]]:
    """Parse @agent: task patterns from an LLM response.

    Recognizes patterns like:
        @cto: implement the auth module
        **CTO**: implement the auth module

    Args:
        response: The LLM response text.
        agent_names: Set of valid agent names to look for.

    Returns:
        List of (agent_name, task_description) tuples.
    """
    delegations = []
    for line in response.split("\n"):
        for agent_name in agent_names:
            escaped = re.escape(agent_name)
            escaped_upper = re.escape(agent_name.upper())
            patterns = [
                rf"@{escaped}\b[:\s]+(.+)",
                rf"\*\*{escaped_upper}\*\*[:\s]+(.+)",
                rf"\*{escaped_upper}\*[:\s]+(.+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    task_text = match.group(1).strip()
                    if len(task_text) > 10:
                        delegations.append((agent_name, task_text))
                    break
    return delegations


def handle_delegations(
    source_agent: str,
    response: str,
    agent_names: set[str],
    add_task_fn,
):
    """Parse delegations and add them to the task board.

    Args:
        source_agent: Name of the agent whose response we're parsing.
        response: The LLM response text.
        agent_names: Set of valid agent names.
        add_task_fn: Callable(title, assigned_to, created_by) to create tasks.
    """
    delegations = parse_delegations(response, agent_names)
    for target_agent, task_desc in delegations:
        if target_agent == source_agent:
            continue
        logger.info(f"Delegation: {source_agent} -> {target_agent}: {task_desc[:80]}")
        add_task_fn(task_desc, assigned_to=target_agent, created_by=source_agent)
