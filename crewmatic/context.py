"""Context injection — memory, Slack channels, local files, project context."""

import logging
import os
import time

from slack_sdk import WebClient

logger = logging.getLogger(__name__)

# Cache to avoid hitting Slack API on every agent call
_cache: dict[str, tuple[float, str]] = {}


def _cached(key: str, loader, ttl: int = 300) -> str:
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < ttl:
        return cached[1]
    result = loader()
    _cache[key] = (now, result)
    return result


def load_agent_memory(agent_name: str, memory_dir: str) -> str:
    """Read agent's persistent memory file."""
    memory_file = os.path.join(memory_dir, f"{agent_name}.md")
    try:
        with open(memory_file) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def load_slack_context(client: WebClient, channel_name_to_id: dict, context_channel: str = "context") -> str:
    """Read messages from a designated context Slack channel."""
    channel_id = channel_name_to_id.get(context_channel)
    if not channel_id:
        return ""
    try:
        result = client.conversations_history(channel=channel_id, limit=50)
        messages = result.get("messages", [])
        if not messages:
            return ""
        parts = []
        for msg in reversed(messages):
            text = msg.get("text", "")
            if text.strip():
                parts.append(text)
        return "\n\n---\n\n".join(parts)
    except Exception as e:
        if "not_in_channel" not in str(e):
            logger.error(f"Failed to read #{context_channel} channel: {e}")
        return ""


def load_team_channels(
    client: WebClient,
    channel_name_to_id: dict,
    exclude_channels: set[str] | None = None,
) -> str:
    """Read recent messages from team channels for cross-team visibility."""
    exclude = exclude_channels or set()
    parts = []
    for ch_name, ch_id in channel_name_to_id.items():
        if ch_name in exclude:
            continue
        try:
            result = client.conversations_history(channel=ch_id, limit=10)
            messages = result.get("messages", [])
            if not messages:
                continue
            lines = []
            for msg in reversed(messages):
                text = msg.get("text", "")
                if text.strip():
                    lines.append(text[:500])
            if lines:
                parts.append(f"### #{ch_name}\n" + "\n".join(lines))
        except Exception as e:
            if "not_in_channel" in str(e):
                continue
            logger.error(f"Failed to read #{ch_name}: {e}")
    return "\n\n".join(parts)


def load_local_context(context_dir: str, max_file_size: int = 8000) -> str:
    """Read local context files from the context directory."""
    if not os.path.isdir(context_dir):
        return ""
    parts = []
    for fname in sorted(os.listdir(context_dir)):
        fpath = os.path.join(context_dir, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath) as f:
                    content = f.read()
                if len(content) > max_file_size:
                    content = content[:max_file_size] + "\n... [truncated]"
                parts.append(f"=== {fname} ===\n{content}")
            except Exception:
                pass
    return "\n\n".join(parts)


def build_prompt(
    agent_name: str,
    message: str,
    receives_context: list[str],
    memory_dir: str,
    context_dir: str,
    task_summary: str,
    client: WebClient | None = None,
    channel_name_to_id: dict | None = None,
    project_context: str = "",
    saved_context: str = "",
    owner_channel: str | None = None,
    cache_ttl: int = 300,
) -> str:
    """Assemble the full prompt with all context injections.

    Args:
        agent_name: Name of the agent.
        message: The core message/task for the agent.
        receives_context: List of context types to inject.
        memory_dir: Path to memory directory.
        context_dir: Path to local context files directory.
        task_summary: Current task board summary string.
        client: Slack WebClient (needed for Slack context).
        channel_name_to_id: Channel name to ID mapping.
        project_context: Active project context string.
        saved_context: Saved working context from previous sessions.
        owner_channel: Agent's own channel name (excluded from team channels).
        cache_ttl: Cache TTL in seconds for Slack API calls.

    Returns:
        Fully assembled prompt string.
    """
    prompt = message
    channel_map = channel_name_to_id or {}

    # Project context
    if "project_context" in receives_context and project_context:
        prompt = f"--- ACTIVE PROJECT ---\n{project_context}\n--- END PROJECT ---\n\n{prompt}"

    # Saved working context (for leader resuming work)
    if "saved_context" in receives_context and saved_context:
        prompt += f"\n\n--- YOUR SAVED CONTEXT (from last session) ---\n{saved_context}\n--- END SAVED CONTEXT ---"

    # Business context (local files + Slack #context channel)
    if "business_context" in receives_context:
        parts = []
        if client:
            slack_ctx = _cached(
                "slack_context",
                lambda: load_slack_context(client, channel_map),
                ttl=cache_ttl,
            )
            if slack_ctx:
                parts.append(f"=== Slack #context channel ===\n{slack_ctx}")
        local_ctx = load_local_context(context_dir)
        if local_ctx:
            parts.append(local_ctx)
        if parts:
            prompt += f"\n\n--- BUSINESS CONTEXT ---\n" + "\n\n".join(parts) + "\n--- END BUSINESS CONTEXT ---"

    # Team channel updates (cache key includes agent to avoid cross-agent stale data)
    if "team_channels" in receives_context and client:
        exclude = {owner_channel} if owner_channel else set()
        cache_key = f"team_channels:{agent_name}"
        team_updates = _cached(
            cache_key,
            lambda: load_team_channels(client, channel_map, exclude),
            ttl=cache_ttl,
        )
        if team_updates:
            prompt += f"\n\n--- TEAM CHANNEL UPDATES ---\n{team_updates}\n--- END TEAM UPDATES ---"

    # Agent memory
    memory = load_agent_memory(agent_name, memory_dir)
    if memory:
        prompt += f"\n\n--- YOUR MEMORY (from previous sessions) ---\n{memory}\n--- END MEMORY ---"
        prompt += f"\n\nIMPORTANT: After completing your task, UPDATE your memory file at {memory_dir}/{agent_name}.md with what you learned, decided, or completed. Keep it concise."

    # Task board
    if task_summary and task_summary not in ("No tasks.", "No open tasks."):
        prompt += f"\n\n--- Current task board ---\n{task_summary}"

    return prompt
