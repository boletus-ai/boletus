"""Setup wizard — interactive Slack onboarding for new Crewmatic crews."""

import enum
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ..llm import LLMRunner
from .channel_manager import ChannelManager
from .crew_generator import generate_crew_yaml, save_crew_yaml
from .prompts import FOLLOWUP_PROMPT, WELCOME_MESSAGE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class SetupState(enum.Enum):
    """Phases the onboarding wizard progresses through."""

    AWAITING_BUSINESS = "awaiting_business"
    AWAITING_DETAILS = "awaiting_details"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CREATING = "creating"
    COMPLETE = "complete"


@dataclass
class SetupSession:
    """Per-user session tracking setup progress."""

    user_id: str
    state: SetupState
    business_description: str = ""
    tech_details: str = ""
    proposed_yaml: str = ""
    proposed_config: dict | None = None
    created_channels: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class SetupWizard:
    """Interactive Slack wizard that guides a user through crew setup.

    The wizard walks the owner through:
    1. Describing their business
    2. Answering follow-up questions about tech stack / roles
    3. Reviewing a generated crew.yaml proposal
    4. Creating Slack channels and saving the config

    Args:
        app: A ``slack_bolt.App`` instance (already configured with a bot token).
        app_token: Slack app-level token for Socket Mode.
        config_dir: Directory where ``crew.yaml`` will be written.
        llm_runner: Any object satisfying the ``LLMRunner`` protocol.
        owner_slack_id: Slack user ID of the owner. Only this user can run setup.
        on_complete: Optional callback invoked after setup finishes successfully.
            Receives the path to the new ``crew.yaml`` as its only argument.
    """

    def __init__(
        self,
        app: App,
        app_token: str,
        config_dir: str,
        llm_runner: LLMRunner,
        owner_slack_id: str = "",
        on_complete: Callable[[str], None] | None = None,
    ):
        self.app = app
        self.app_token = app_token
        self.config_dir = config_dir
        self.llm_runner = llm_runner
        self.owner_slack_id = owner_slack_id
        self.on_complete = on_complete

        self.sessions: dict[str, SetupSession] = {}

        self._register_handlers()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_or_create_session(self, user_id: str) -> SetupSession:
        """Return the existing session for *user_id* or create a new one."""
        if user_id not in self.sessions:
            self.sessions[user_id] = SetupSession(
                user_id=user_id,
                state=SetupState.AWAITING_BUSINESS,
            )
        return self.sessions[user_id]

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------

    def _llm_call(self, system_prompt: str, user_message: str) -> str:
        """Thin wrapper around the LLM runner with sensible defaults."""
        return self.llm_runner.call(
            system_prompt=system_prompt,
            user_message=user_message,
            model="sonnet",
        )

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    def _handle_message(self, user_id: str, text: str, channel_id: str, thread_ts: str, say: Callable):
        """Main router — dispatches to the handler for the current session state."""
        if self.owner_slack_id and user_id != self.owner_slack_id:
            say(
                text="Only the workspace owner can run the setup wizard.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return

        session = self._get_or_create_session(user_id)

        if session.state == SetupState.AWAITING_BUSINESS:
            self._handle_business(session, text, channel_id, thread_ts, say)

        elif session.state == SetupState.AWAITING_DETAILS:
            self._handle_details(session, text, channel_id, thread_ts, say)

        elif session.state == SetupState.AWAITING_CONFIRMATION:
            self._handle_confirmation_text(session, text, channel_id, thread_ts, say)

        elif session.state == SetupState.CREATING:
            say(
                text="Hold tight — I'm setting things up right now...",
                channel=channel_id,
                thread_ts=thread_ts,
            )

        elif session.state == SetupState.COMPLETE:
            say(
                text=(
                    "Setup is already complete! Restart with `crewmatic run` "
                    "to bring your AI team online."
                ),
                channel=channel_id,
                thread_ts=thread_ts,
            )

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_business(
        self,
        session: SetupSession,
        text: str,
        channel_id: str,
        thread_ts: str,
        say: Callable,
    ):
        """AWAITING_BUSINESS -> save description, ask follow-ups."""
        session.business_description = text
        session.state = SetupState.AWAITING_DETAILS

        say(
            text="Got it! Let me think of a few follow-up questions...",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        followup_prompt = FOLLOWUP_PROMPT.format(business_description=text)
        try:
            followup = self._llm_call(
                "You are a friendly setup assistant helping someone configure an AI team.",
                followup_prompt,
            )
            say(text=followup, channel=channel_id, thread_ts=thread_ts)
        except Exception as exc:
            logger.error(f"LLM follow-up call failed: {exc}")
            say(
                text=(
                    "I had trouble generating follow-up questions. "
                    "No worries — just tell me about your tech stack, "
                    "the roles you need, and any existing codebases."
                ),
                channel=channel_id,
                thread_ts=thread_ts,
            )

    def _handle_details(
        self,
        session: SetupSession,
        text: str,
        channel_id: str,
        thread_ts: str,
        say: Callable,
    ):
        """AWAITING_DETAILS -> generate crew.yaml, show proposal."""
        session.tech_details = text

        say(
            text="Thanks! Generating your AI team configuration...",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        try:
            raw_yaml, parsed = generate_crew_yaml(
                llm_call_fn=self._llm_call,
                business_desc=session.business_description,
                tech_details=session.tech_details,
            )
        except Exception as exc:
            logger.error(f"Crew generation failed: {exc}")
            say(
                text="Something went wrong generating the config. Please try again.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return

        if "_error" in parsed:
            say(
                text=f"I generated a config but it has issues: {parsed['_error']}\nLet me try again — please rephrase or add more details.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            session.state = SetupState.AWAITING_DETAILS
            return

        session.proposed_yaml = raw_yaml
        session.proposed_config = parsed
        session.state = SetupState.AWAITING_CONFIRMATION

        self._show_proposal(channel_id, thread_ts, session, say)

    def _handle_confirmation_text(
        self,
        session: SetupSession,
        text: str,
        channel_id: str,
        thread_ts: str,
        say: Callable,
    ):
        """AWAITING_CONFIRMATION -> parse free-text confirmation intent."""
        text_lower = text.lower().strip()

        affirmative = ("yes", "looks good", "confirm", "ok", "go", "approve", "lgtm", "ship it", "do it")
        restart = ("start over", "reset", "restart", "redo")
        modify = ("change", "modify", "edit", "update", "tweak")

        if any(word in text_lower for word in affirmative):
            self._handle_confirm(session, channel_id, thread_ts, say)
        elif any(word in text_lower for word in restart):
            self._handle_restart(session, channel_id, thread_ts, say)
        elif any(word in text_lower for word in modify):
            say(
                text="Sure, what would you like to change? Describe the modification and I'll update the config.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            # Stay in AWAITING_CONFIRMATION — next message will be treated as modification
            # unless it matches an affirmative/restart keyword
        else:
            # Treat as a modification request
            self._handle_modification(session, text, channel_id, thread_ts, say)

    def _handle_modification(
        self,
        session: SetupSession,
        text: str,
        channel_id: str,
        thread_ts: str,
        say: Callable,
    ):
        """Re-generate the crew config incorporating the user's change request."""
        combined_details = (
            f"{session.tech_details}\n\n"
            f"Additional changes requested: {text}"
        )
        session.tech_details = combined_details

        say(
            text="Regenerating with your changes...",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        try:
            raw_yaml, parsed = generate_crew_yaml(
                llm_call_fn=self._llm_call,
                business_desc=session.business_description,
                tech_details=combined_details,
            )
        except Exception as exc:
            logger.error(f"Modification generation failed: {exc}")
            say(
                text="Something went wrong. Please try describing the change differently.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return

        if "_error" in parsed:
            say(
                text=f"The updated config has issues: {parsed['_error']}\nPlease rephrase your change.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return

        session.proposed_yaml = raw_yaml
        session.proposed_config = parsed
        self._show_proposal(channel_id, thread_ts, session, say)

    # ------------------------------------------------------------------
    # Proposal display
    # ------------------------------------------------------------------

    def _show_proposal(self, channel_id: str, thread_ts: str, session: SetupSession, say: Callable):
        """Post the crew proposal using Slack Block Kit."""
        config = session.proposed_config
        if not config:
            return

        agents = config.get("agents", {})
        crew_name = config.get("name", "Your AI Team")

        blocks: list[dict] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Proposed crew: {crew_name}"},
            },
            {"type": "divider"},
        ]

        for name, agent_def in agents.items():
            role = agent_def.get("role", "worker")
            channel = agent_def.get("channel", name)
            prompt_preview = agent_def.get("system_prompt", "")[:120].replace("\n", " ")
            model = agent_def.get("model", "sonnet")

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{name.upper()}* ({role} / {model})\n"
                        f"Channel: `#{channel}`\n"
                        f"_{prompt_preview}..._"
                    ),
                },
            })

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Looks good!"},
                    "style": "primary",
                    "action_id": "setup_confirm",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Make changes"},
                    "action_id": "setup_modify",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Start over"},
                    "style": "danger",
                    "action_id": "setup_restart",
                },
            ],
        })

        # Also post the raw YAML in a collapsible snippet
        say(text=f"Here's your proposed crew config:", blocks=blocks, channel=channel_id, thread_ts=thread_ts)

        # Post YAML as a snippet for review
        try:
            self.app.client.files_upload_v2(
                channel=channel_id,
                content=session.proposed_yaml,
                filename="crew.yaml",
                title="Proposed crew.yaml",
                thread_ts=thread_ts,
            )
        except Exception as exc:
            # Fallback: post as code block
            logger.warning(f"File upload failed, falling back to code block: {exc}")
            yaml_preview = session.proposed_yaml[:3000]
            say(
                text=f"```\n{yaml_preview}\n```",
                channel=channel_id,
                thread_ts=thread_ts,
            )

    # ------------------------------------------------------------------
    # Confirmation / creation
    # ------------------------------------------------------------------

    def _handle_confirm(self, session: SetupSession, channel_id: str, thread_ts: str, say: Callable):
        """Create channels, save config, and finalize setup."""
        if session.state == SetupState.CREATING:
            return
        session.state = SetupState.CREATING

        say(
            text="Setting up your AI team — creating channels and saving config...",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        # 1. Create channels
        channel_mgr = ChannelManager(self.app.client)

        def _progress(agent_name: str, success: bool, info: str):
            if success:
                say(
                    text=f"Created channel for *{agent_name.upper()}* ({info})",
                    channel=channel_id,
                    thread_ts=thread_ts,
                )
                session.created_channels.append(agent_name)
            else:
                say(
                    text=f"Warning: could not create channel for {agent_name}: {info}",
                    channel=channel_id,
                    thread_ts=thread_ts,
                )

        channel_mgr.create_channels_for_crew(session.proposed_config, progress_callback=_progress)

        # 2. Save crew.yaml
        try:
            config_path = save_crew_yaml(self.config_dir, session.proposed_yaml)
        except Exception as exc:
            logger.error(f"Failed to save crew.yaml: {exc}")
            say(
                text=f"Failed to save config: {exc}",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            session.state = SetupState.AWAITING_CONFIRMATION
            return

        session.state = SetupState.COMPLETE

        # 3. Success message
        say(
            text=(
                "Your AI team is ready! "
                f"Config saved to `{config_path}`.\n\n"
                "Restart with `crewmatic run` or I'll start it automatically in 5 seconds..."
            ),
            channel=channel_id,
            thread_ts=thread_ts,
        )

        # 4. Trigger on_complete callback (e.g. auto-start the full bot)
        if self.on_complete:
            def _delayed_complete():
                time.sleep(5)
                try:
                    self.on_complete(config_path)
                except Exception as exc:
                    logger.error(f"on_complete callback failed: {exc}")

            threading.Thread(target=_delayed_complete, daemon=True, name="setup-complete").start()

    def _handle_restart(self, session: SetupSession, channel_id: str, thread_ts: str, say: Callable):
        """Reset the session back to the beginning."""
        self.sessions[session.user_id] = SetupSession(
            user_id=session.user_id,
            state=SetupState.AWAITING_BUSINESS,
        )
        say(
            text="No problem — let's start fresh.\n\n" + WELCOME_MESSAGE,
            channel=channel_id,
            thread_ts=thread_ts,
        )

    # ------------------------------------------------------------------
    # Slack event handlers
    # ------------------------------------------------------------------

    def _register_handlers(self):
        """Wire up Slack event and action handlers."""

        @self.app.event("app_mention")
        def handle_mention(event, say):
            import re
            user_id = event.get("user", "")
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", event.get("text", "")).strip()
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts", event.get("ts", ""))
            self._handle_message(user_id, text, channel_id, thread_ts, say)

        @self.app.event("message")
        def handle_message(event, say):
            # Only handle DMs (channel type "im")
            if event.get("channel_type") != "im":
                return
            if event.get("subtype"):
                return
            if event.get("bot_id"):
                return

            user_id = event.get("user", "")
            text = event.get("text", "")
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts", event.get("ts", ""))
            self._handle_message(user_id, text, channel_id, thread_ts, say)

        @self.app.action("setup_confirm")
        def handle_confirm_action(ack, body):
            ack()
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            # Use message ts as thread_ts for consistency
            thread_ts = body.get("message", {}).get("ts", "")

            session = self.sessions.get(user_id)
            if not session or session.state != SetupState.AWAITING_CONFIRMATION:
                return

            def _say(**kwargs):
                self.app.client.chat_postMessage(**kwargs)

            self._handle_confirm(session, channel_id, thread_ts, _say)

        @self.app.action("setup_modify")
        def handle_modify_action(ack, body):
            ack()
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            thread_ts = body.get("message", {}).get("ts", "")

            session = self.sessions.get(user_id)
            if not session:
                return

            self.app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="Sure, what would you like to change? Describe the modification and I'll update the config.",
            )

        @self.app.action("setup_restart")
        def handle_restart_action(ack, body):
            ack()
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            thread_ts = body.get("message", {}).get("ts", "")

            session = self.sessions.get(user_id)
            if not session:
                return

            def _say(**kwargs):
                self.app.client.chat_postMessage(**kwargs)

            self._handle_restart(session, channel_id, thread_ts, _say)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def start(self):
        """Post welcome message and start listening for Slack events."""
        # Send welcome DM to owner if configured
        if self.owner_slack_id:
            try:
                dm = self.app.client.conversations_open(users=[self.owner_slack_id])
                dm_channel = dm["channel"]["id"]
                self.app.client.chat_postMessage(
                    channel=dm_channel,
                    text=WELCOME_MESSAGE,
                )
                logger.info(f"Sent welcome DM to owner {self.owner_slack_id}")
            except Exception as exc:
                logger.error(f"Failed to send welcome DM: {exc}")

        logger.info("Setup wizard started — waiting for owner input")
        handler = SocketModeHandler(self.app, self.app_token)
        handler.start()
