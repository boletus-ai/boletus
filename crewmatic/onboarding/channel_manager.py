"""Slack channel creation and management for crew onboarding."""

import logging
import re
from typing import Callable

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

# Slack channel name constraints
_CHANNEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]?$")
MAX_CHANNEL_NAME_LEN = 80


class ChannelManager:
    """Creates and manages Slack channels for a Crewmatic crew."""

    def __init__(self, client: WebClient):
        self.client = client

    def create_channel(self, name: str, purpose: str = "") -> tuple[bool, str, str]:
        """Create a public Slack channel.

        Args:
            name: Channel name (lowercase, alphanumeric + hyphens, max 80 chars).
            purpose: Optional channel purpose/description.

        Returns:
            A tuple of ``(success, channel_id_or_empty, error_message_or_empty)``.
        """
        name = _sanitize_channel_name(name)

        if not name:
            return False, "", "Invalid channel name after sanitization."

        try:
            result = self.client.conversations_create(name=name, is_private=False)
            channel_id = result["channel"]["id"]

            if purpose:
                try:
                    self.client.conversations_setPurpose(
                        channel=channel_id, purpose=purpose[:250]
                    )
                except SlackApiError:
                    pass  # Non-critical — purpose is cosmetic

            logger.info(f"Created channel #{name} ({channel_id})")
            return True, channel_id, ""

        except SlackApiError as exc:
            error_code = exc.response.get("error", "")

            if error_code == "name_taken":
                # Channel already exists — try to find and join it
                logger.info(f"Channel #{name} already exists, attempting to join")
                channel_id = self._find_channel_by_name(name)
                if channel_id:
                    joined = self.join_channel(channel_id)
                    if joined:
                        return True, channel_id, ""
                    return False, "", f"Channel #{name} exists but could not join."
                return False, "", f"Channel #{name} exists but could not find its ID."

            if error_code == "restricted_action":
                return False, "", (
                    f"Bot lacks permission to create channel #{name}. "
                    "Grant the channels:manage scope."
                )

            if error_code == "invalid_name":
                return False, "", f"Slack rejected channel name '{name}' as invalid."

            logger.error(f"Slack API error creating #{name}: {exc}")
            return False, "", f"Slack error: {error_code}"

    def create_channels_for_crew(
        self,
        crew_config: dict,
        progress_callback: Callable[[str, bool, str], None] | None = None,
    ) -> dict[str, str]:
        """Create Slack channels for the crew config.

        Multiple agents may share the same channel (e.g. workers share their
        manager's channel). Channels are deduplicated so each is created only once.

        Args:
            crew_config: Parsed crew.yaml dict (must have ``agents`` key).
            progress_callback: Called after each channel with
                ``(channel_name, success, channel_id_or_error)``.

        Returns:
            Mapping of ``{channel_name: channel_id}`` for successfully created channels.
        """
        agents = crew_config.get("agents", {})
        created: dict[str, str] = {}

        # Collect unique channels and the agents that use them
        unique_channels: dict[str, list[str]] = {}
        for agent_name, agent_def in agents.items():
            channel_name = agent_def.get("channel", agent_name)
            unique_channels.setdefault(channel_name, []).append(agent_name)

        for channel_name, agent_names in unique_channels.items():
            team_desc = ", ".join(n.upper() for n in agent_names)
            purpose = f"Team channel for {team_desc}"

            success, channel_id, error = self.create_channel(channel_name, purpose)

            if success:
                created[channel_name] = channel_id
                if progress_callback:
                    progress_callback(channel_name, True, channel_id)
            else:
                logger.warning(f"Failed to create channel #{channel_name}: {error}")
                if progress_callback:
                    progress_callback(channel_name, False, error)

        return created

    def post_welcome_message(
        self,
        channel_id: str,
        channel_name: str,
        agent_name: str,
        role: str,
    ) -> bool:
        """Post and pin a welcome message in a newly created agent channel.

        Args:
            channel_id: Slack channel ID.
            channel_name: Human-readable channel name (without ``#``).
            agent_name: Display name for the agent (e.g. ``CEO``).
            role: Agent role — ``leader``, ``manager``, or ``worker``.

        Returns:
            True if the message was posted (pin failure is non-critical).
        """
        role_descriptions = {
            "leader": "Sets strategy, delegates work to the team, and reports progress to you.",
            "manager": "Manages technical decisions, reviews work, and coordinates the dev team.",
            "worker": "Executes tasks assigned by the team leads.",
        }
        description = role_descriptions.get(role, role_descriptions["worker"])
        display_name = agent_name.upper()

        text = (
            f"Welcome to #{channel_name}\n\n"
            f"This channel is operated by *{display_name}* ({role}).\n\n"
            f"{description}\n\n"
            f"*How to interact:*\n"
            f"• Send a message here to talk to {display_name}\n"
            f"• Type `tasks` to see the task board\n"
            f"• Type `help` for all commands\n\n"
            f"{display_name} will also work autonomously — check back for updates."
        )

        try:
            result = self.client.chat_postMessage(channel=channel_id, text=text)
            message_ts = result["ts"]

            # Pin the welcome message
            try:
                self.client.pins_add(channel=channel_id, timestamp=message_ts)
            except SlackApiError as exc:
                logger.warning(f"Failed to pin welcome message in #{channel_name}: {exc}")

            logger.info(f"Posted welcome message in #{channel_name}")
            return True
        except SlackApiError as exc:
            logger.error(f"Failed to post welcome message in #{channel_name}: {exc}")
            return False

    def join_channel(self, channel_id: str) -> bool:
        """Join an existing channel.

        Args:
            channel_id: The Slack channel ID to join.

        Returns:
            True if successfully joined, False otherwise.
        """
        try:
            self.client.conversations_join(channel=channel_id)
            logger.info(f"Joined channel {channel_id}")
            return True
        except SlackApiError as exc:
            logger.error(f"Failed to join channel {channel_id}: {exc}")
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_channel_by_name(self, name: str) -> str | None:
        """Look up a channel ID by name."""
        try:
            cursor = None
            while True:
                kwargs: dict = {"types": "public_channel", "limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor
                result = self.client.conversations_list(**kwargs)
                for ch in result["channels"]:
                    if ch["name"] == name:
                        return ch["id"]
                cursor = result.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        except SlackApiError as exc:
            logger.error(f"Failed to search for channel #{name}: {exc}")
        return None


def _sanitize_channel_name(name: str) -> str:
    """Normalize a string into a valid Slack channel name.

    Rules: lowercase, only ``[a-z0-9-]``, max 80 chars, no leading/trailing hyphens.
    """
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    name = name[:MAX_CHANNEL_NAME_LEN]
    name = name.strip("-")
    return name
