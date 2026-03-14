"""Setup wizard — interactive Slack onboarding for new Crewmatic crews."""

import enum
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ..integrations import (
    list_integrations, match_integrations_from_description,
    get_integration, save_credentials_to_env,
)
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
    AWAITING_INTEGRATIONS = "awaiting_integrations"
    AWAITING_CREDENTIALS = "awaiting_credentials"
    AWAITING_EMAIL_PERMISSION = "awaiting_email_permission"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_MODIFICATION = "awaiting_modification"
    CREATING = "creating"
    COMPLETE = "complete"


@dataclass
class SetupSession:
    """Per-user session tracking setup progress."""

    user_id: str
    state: SetupState
    business_description: str = ""
    tech_details: str = ""
    uploaded_docs: list[str] = field(default_factory=list)
    selected_integrations: list[str] = field(default_factory=list)
    pending_credentials: list[dict] = field(default_factory=list)  # integrations waiting for creds
    collected_credentials: dict = field(default_factory=dict)  # env_var -> value
    credential_total: int = 0  # total integrations to collect credentials for
    proposed_yaml: str = ""
    proposed_config: dict | None = None
    email_mode: str = "drafts"  # "drafts" or "send"
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
            Receives ``(config_path, business_description)``.
    """

    def __init__(
        self,
        app: App,
        app_token: str,
        config_dir: str,
        llm_runner: LLMRunner,
        owner_slack_id: str = "",
        on_complete: Callable[[str, str], None] | None = None,
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
    # File upload handling
    # ------------------------------------------------------------------

    def _process_files(
        self,
        files: list[dict],
        session: SetupSession,
        channel_id: str,
        thread_ts: str,
        say: Callable,
    ) -> str:
        """Download, parse, and save uploaded files. Returns extracted text."""
        from .file_parser import process_slack_files, SUPPORTED_EXTENSIONS
        from pathlib import Path

        bot_token = self.app.client.token

        # Filter to supported files and tell user what we're processing
        supported = [f for f in files if Path(f.get("name", "")).suffix.lower() in SUPPORTED_EXTENSIONS]
        unsupported = [f for f in files if f not in supported]

        if unsupported:
            names = ", ".join(f.get("name", "?") for f in unsupported)
            say(
                text=f"I can't read these file types yet: {names}\nSupported: .pdf, .docx, .md, .txt, .csv, .json, .yaml",
                channel=channel_id,
                thread_ts=thread_ts,
            )

        if not supported:
            return ""

        names = ", ".join(f.get("name", "?") for f in supported)
        say(
            text=f"Reading {names}...",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        results = process_slack_files(
            files=supported,
            bot_token=bot_token,
            context_dir=os.path.join(self.config_dir, "context"),
        )

        if not results:
            say(
                text="I couldn't extract text from the uploaded files. Try a different format.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return ""

        # Build combined text and track in session
        parts = []
        for filename, text in results:
            session.uploaded_docs.append(filename)
            parts.append(f"--- Content from {filename} ---\n{text}")

        combined = "\n\n".join(parts)

        say(
            text=f"Got it! Extracted content from {len(results)} file(s). I'll use this to design your team.",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        return combined

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    def _handle_message(self, user_id: str, text: str, channel_id: str, thread_ts: str, say: Callable, files: list | None = None, message_ts: str = ""):
        """Main router — dispatches to the handler for the current session state."""
        if self.owner_slack_id and user_id != self.owner_slack_id:
            say(
                text="Only the workspace owner can run the setup wizard.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return

        session = self._get_or_create_session(user_id)

        # Process uploaded files
        if files:
            file_text = self._process_files(files, session, channel_id, thread_ts, say)
            if file_text:
                text = f"{text}\n\n{file_text}" if text else file_text

        if session.state == SetupState.AWAITING_BUSINESS:
            self._handle_business(session, text, channel_id, thread_ts, say)

        elif session.state == SetupState.AWAITING_DETAILS:
            self._handle_details(session, text, channel_id, thread_ts, say)

        elif session.state == SetupState.AWAITING_INTEGRATIONS:
            # Text messages during integration selection are ignored;
            # the user interacts via Block Kit buttons/checkboxes.
            say(
                text="Please use the checkboxes above to select integrations, then click *Continue* (or *Skip*).",
                channel=channel_id,
                thread_ts=thread_ts,
            )

        elif session.state == SetupState.AWAITING_EMAIL_PERMISSION:
            self._handle_email_permission(session, text, channel_id, thread_ts, say)

        elif session.state == SetupState.AWAITING_CREDENTIALS:
            self._handle_credential_input(session, text, channel_id, thread_ts, say, message_ts=message_ts)

        elif session.state == SetupState.AWAITING_MODIFICATION:
            self._handle_modification(session, text, channel_id, thread_ts, say)

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
        if not text or not text.strip():
            say(
                text="I didn't get any content. Please describe your business or upload a document (PDF, text).",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return
        session.business_description = text
        session.state = SetupState.AWAITING_DETAILS

        say(
            text="*Step 2/5 — A few more questions*\n\nGot it! Let me think of a few follow-up questions...",
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
        """AWAITING_DETAILS -> show integration selection step."""
        # Handle retry after generation failure
        if text.strip().lower() == "retry" and session.selected_integrations is not None:
            self._generate_and_show_proposal(session, channel_id, thread_ts, say)
            return
        session.tech_details = text
        self._show_integrations(session, channel_id, thread_ts, say)

    def _generate_and_show_proposal(
        self,
        session: SetupSession,
        channel_id: str,
        thread_ts: str,
        say: Callable,
    ):
        """Generate crew.yaml from session context and show proposal."""
        say(
            text="*Step 4/5 — Review your team*\n\nThanks! Generating your AI team configuration...",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        try:
            raw_yaml, parsed = generate_crew_yaml(
                llm_call_fn=self._llm_call,
                business_desc=session.business_description,
                tech_details=session.tech_details,
                integrations=session.selected_integrations or None,
            )
        except Exception as exc:
            logger.error(f"Crew generation failed: {exc}")
            error_hint = str(exc)
            if "timeout" in error_hint.lower() or "didn't respond" in error_hint.lower():
                error_hint = "Claude took too long to respond. This can happen on first run. Please type *retry* to try again."
            else:
                error_hint = f"Error: {error_hint}\nPlease type *retry* to try again."
            say(
                text=f"Something went wrong generating the config.\n{error_hint}",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            session.state = SetupState.AWAITING_DETAILS
            return

        # Inject email_mode into settings
        if "gmail" in session.selected_integrations and parsed and "_error" not in parsed:
            settings = parsed.setdefault("settings", {})
            settings["email_mode"] = session.email_mode
            # Re-serialize with the injected setting
            import yaml
            raw_yaml = yaml.dump(parsed, default_flow_style=False, allow_unicode=True, sort_keys=False)

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

    # ------------------------------------------------------------------
    # Integration selection
    # ------------------------------------------------------------------

    def _show_integrations(
        self,
        session: SetupSession,
        channel_id: str,
        thread_ts: str,
        say: Callable,
    ):
        """Show available integrations as Block Kit checkboxes."""
        session.state = SetupState.AWAITING_INTEGRATIONS

        all_integrations = list_integrations()
        suggested = match_integrations_from_description(session.business_description)

        if not all_integrations:
            # No integrations available — skip straight to crew generation
            self._generate_and_show_proposal(session, channel_id, thread_ts, say)
            return

        options = []
        initial_options = []
        for integration in sorted(all_integrations, key=lambda i: i["key"]):
            key = integration["key"]
            option = {
                "text": {"type": "plain_text", "text": key},
                "description": {"type": "plain_text", "text": integration.get("description", "")[:75]},
                "value": key,
            }
            options.append(option)
            if key in suggested:
                initial_options.append(option)

        # Slack limits checkbox groups to 10 options — split into chunks
        CHUNK_SIZE = 10
        option_chunks = [options[i:i + CHUNK_SIZE] for i in range(0, len(options), CHUNK_SIZE)]

        blocks: list[dict] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Step 3/5 — Choose integrations"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "I auto-detected some integrations based on your description. "
                        "Check the ones you want your AI team to use:"
                    ),
                },
            },
        ]

        for idx, chunk in enumerate(option_chunks):
            chunk_initial = [o for o in chunk if o in initial_options]
            blocks.append({
                "type": "actions",
                "block_id": f"integration_checkboxes_block_{idx}",
                "elements": [
                    {
                        "type": "checkboxes",
                        "action_id": f"integration_checkboxes_{idx}",
                        "options": chunk,
                        **({"initial_options": chunk_initial} if chunk_initial else {}),
                    },
                ],
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Continue"},
                    "style": "primary",
                    "action_id": "setup_integrations_confirm",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Skip"},
                    "action_id": "setup_skip_integrations",
                },
            ],
        })

        say(
            text="*Step 3/5 — Choose integrations*\n\nSelect the integrations you want your AI team to use:",
            blocks=blocks,
            channel=channel_id,
            thread_ts=thread_ts,
        )

    # ------------------------------------------------------------------
    # Credential collection (Step 3b)
    # ------------------------------------------------------------------

    def _start_credential_collection(
        self, session: SetupSession, channel_id: str, thread_ts: str, say: Callable
    ):
        """Walk through each selected integration and collect credentials."""
        # Identify which integrations use Claude.ai connectors (no credentials needed)
        claude_ai_integrations = []
        for name in session.selected_integrations:
            integration = get_integration(name)
            if integration and integration.get("claude_ai_tools") and not integration.get("env_vars"):
                claude_ai_integrations.append(integration["name"])

        if claude_ai_integrations:
            names = ", ".join(claude_ai_integrations)
            say(
                text=(
                    f"*{names}* — these work through your Claude account.\n"
                    "Make sure you've connected them at claude.ai → Settings → Integrations.\n"
                    "No API keys needed — your agents get automatic access."
                ),
                channel=channel_id,
                thread_ts=thread_ts,
            )

        # Ask about email permissions if Gmail is selected
        if "gmail" in session.selected_integrations:
            say(
                text=(
                    "*Email permissions:* What should your AI team be allowed to do with email?\n\n"
                    "• Type `drafts` — agents can only create drafts, you review and send manually (recommended)\n"
                    "• Type `send` — agents can send emails directly on your behalf\n\n"
                    "Default is `drafts` (safer). You can change this later in crew.yaml."
                ),
                channel=channel_id,
                thread_ts=thread_ts,
            )
            session.state = SetupState.AWAITING_EMAIL_PERMISSION
            return

        self._continue_credential_collection(session, channel_id, thread_ts, say)

    def _handle_email_permission(
        self, session: SetupSession, text: str, channel_id: str, thread_ts: str, say: Callable
    ):
        """Handle the email permission choice (drafts vs send)."""
        choice = text.strip().lower()
        if choice in ("send", "yes", "allow"):
            session.email_mode = "send"
            say(
                text="Got it — agents *can send emails directly*. Be careful, review agent activity regularly.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
        else:
            session.email_mode = "drafts"
            say(
                text="Got it — agents will *only create drafts*. You review and send manually.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
        # Continue to credential collection
        self._continue_credential_collection(session, channel_id, thread_ts, say)

    def _continue_credential_collection(
        self, session: SetupSession, channel_id: str, thread_ts: str, say: Callable
    ):
        """Build pending credentials list and start collection (shared by email permission and direct path)."""
        pending = []
        for name in session.selected_integrations:
            integration = get_integration(name)
            if integration and integration.get("env_vars"):
                all_set = all(os.environ.get(v) for v in integration["env_vars"])
                if all_set:
                    say(
                        text=f"*{integration['name']}* — already configured, skipping.",
                        channel=channel_id,
                        thread_ts=thread_ts,
                    )
                else:
                    pending.append({"key": name, **integration})

        if not pending:
            self._generate_and_show_proposal(session, channel_id, thread_ts, say)
            return

        session.pending_credentials = pending
        session.credential_total = len(pending)
        session.state = SetupState.AWAITING_CREDENTIALS
        self._ask_next_credential(session, channel_id, thread_ts, say)

    def _ask_next_credential(
        self, session: SetupSession, channel_id: str, thread_ts: str, say: Callable
    ):
        """Ask for the next integration's credentials."""
        if not session.pending_credentials:
            # All collected — save and continue
            if session.collected_credentials:
                save_credentials_to_env(self.config_dir, session.collected_credentials)
                # Also set in current process so agents can use them immediately
                for key, val in session.collected_credentials.items():
                    os.environ[key] = val
                say(
                    text=f"Credentials saved. {len(session.collected_credentials)} keys configured.",
                    channel=channel_id,
                    thread_ts=thread_ts,
                )
            self._generate_and_show_proposal(session, channel_id, thread_ts, say)
            return

        current = session.pending_credentials[0]
        setup_msg = current.get("setup_message", f"Please provide credentials for {current['name']}.")

        remaining = len(session.pending_credentials)
        total = session.credential_total
        current_num = total - remaining + 1
        say(
            text=f"*Connecting {current_num}/{total}* — {setup_msg}",
            channel=channel_id,
            thread_ts=thread_ts,
        )

    def _handle_credential_input(
        self, session: SetupSession, text: str, channel_id: str, thread_ts: str, say: Callable, message_ts: str = ""
    ):
        """Process a credential pasted by the user."""
        if not session.pending_credentials:
            return

        current = session.pending_credentials[0]
        env_vars = current.get("env_vars", [])
        token = text.strip()

        # Skip if user says "skip"
        if token.lower() in ("skip", "later", "next"):
            # Remove any partially-collected vars for this integration
            for var in env_vars:
                session.collected_credentials.pop(var, None)
            say(
                text=f"Skipping *{current['name']}* — no worries, you can connect it later by running `crewmatic init` or typing `integrations` in Slack.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            session.pending_credentials.pop(0)
            self._ask_next_credential(session, channel_id, thread_ts, say)
            return

        # Gmail needs two credentials — address + password
        if current["key"] == "gmail" and "GMAIL_APP_PASSWORD" not in session.collected_credentials:
            session.collected_credentials["GMAIL_APP_PASSWORD"] = token
            say(
                text="Got it. Now *paste your Gmail address* (e.g. you@gmail.com):",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return
        elif current["key"] == "gmail" and "GMAIL_ADDRESS" not in session.collected_credentials:
            session.collected_credentials["GMAIL_ADDRESS"] = token
        else:
            # Find the next uncollected env var for this integration
            target_var = None
            for var in env_vars:
                if var not in session.collected_credentials:
                    target_var = var
                    break
            if target_var:
                session.collected_credentials[target_var] = token

            # If there are more env vars to collect, ask for the next one
            remaining = [v for v in env_vars if v not in session.collected_credentials]
            if remaining:
                next_var = remaining[0]
                say(
                    text=f"Got it. Now paste your *{next_var}*:",
                    channel=channel_id,
                    thread_ts=thread_ts,
                )
                return

        # Try to delete the message with the token for security
        delete_ts = message_ts or thread_ts
        if delete_ts and delete_ts != thread_ts:
            try:
                self.app.client.chat_delete(channel=channel_id, ts=delete_ts)
            except Exception:
                pass  # Can't delete user messages without admin scope — that's ok

        say(
            text=f"*{current['name']}* connected!",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        session.pending_credentials.pop(0)
        self._ask_next_credential(session, channel_id, thread_ts, say)

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
            session.state = SetupState.AWAITING_MODIFICATION
            say(
                text="Sure, what would you like to change? Describe the modification and I'll update the config.",
                channel=channel_id,
                thread_ts=thread_ts,
            )
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
                integrations=session.selected_integrations or None,
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
                "text": {"type": "plain_text", "text": f"Step 4/5 — Review your team: {crew_name}"},
            },
            {"type": "divider"},
        ]

        for name, agent_def in agents.items():
            role = agent_def.get("role", "worker")
            channel = agent_def.get("channel", name)
            # Show first meaningful line of system prompt (skip generic rules)
            raw_prompt = agent_def.get("system_prompt", "")
            prompt_lines = [
                line.strip() for line in raw_prompt.split("\n")
                if line.strip() and not line.strip().startswith("Always start your messages")
                and not line.strip().startswith("When you have important")
            ]
            prompt_preview = (prompt_lines[0] if prompt_lines else raw_prompt[:120])[:120]
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
        say(text=f"*Step 4/5 — Review your team*\n\nHere's your proposed crew config:", blocks=blocks, channel=channel_id, thread_ts=thread_ts)

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
            text="*Step 5/5 — Creating your team*\n\nSetting up your AI team — creating channels and saving config...",
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

        created_channels = channel_mgr.create_channels_for_crew(
            session.proposed_config, progress_callback=_progress,
        )

        # 1b. Invite owner to all created channels
        owner_id = session.user_id
        for ch_name, ch_id in created_channels.items():
            try:
                self.app.client.conversations_invite(channel=ch_id, users=owner_id)
                logger.info(f"Invited owner to #{ch_name}")
            except Exception as exc:
                # already_in_channel or other non-critical error
                if "already_in_channel" not in str(exc):
                    logger.warning(f"Could not invite owner to #{ch_name}: {exc}")

        # 1c. Post pinned welcome messages in each created channel
        agents = session.proposed_config.get("agents", {})
        for agent_name, agent_def in agents.items():
            channel_name = agent_def.get("channel", agent_name)
            if channel_name in created_channels:
                role = agent_def.get("role", "worker")
                channel_mgr.post_welcome_message(
                    channel_id=created_channels[channel_name],
                    channel_name=channel_name,
                    agent_name=agent_name,
                    role=role,
                )

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

        # 2b. Initialize git repo if not already one (agents need it for code)
        import subprocess
        if not os.path.isdir(os.path.join(self.config_dir, ".git")):
            try:
                subprocess.run(
                    ["git", "init"], cwd=self.config_dir,
                    capture_output=True, timeout=10,
                )
                # Create .gitignore for common excludes
                gitignore_path = os.path.join(self.config_dir, ".gitignore")
                if not os.path.exists(gitignore_path):
                    with open(gitignore_path, "w") as f:
                        f.write(".env\nnode_modules/\n.venv/\n__pycache__/\n*.pyc\ndata/\n")
                subprocess.run(
                    ["git", "add", "-A"], cwd=self.config_dir,
                    capture_output=True, timeout=10,
                )
                subprocess.run(
                    ["git", "commit", "-m", "Initial commit — crewmatic setup"],
                    cwd=self.config_dir, capture_output=True, timeout=10,
                )
                logger.info("Initialized git repo for project codebase")
            except Exception as exc:
                logger.warning(f"Could not initialize git repo: {exc}")

        session.state = SetupState.COMPLETE

        # 3. Success message
        say(
            text=(
                "Your AI team is ready! "
                f"Config saved to `{config_path}`.\n\n"
                "Starting the bot in 5 seconds — your business plan will be forwarded to the CEO automatically."
            ),
            channel=channel_id,
            thread_ts=thread_ts,
        )

        # 3b. Send getting-started guide as a DM to the owner
        self._send_getting_started_dm()

        # 4. Stop wizard handler and start the bot.
        # Two SocketModeHandlers on the same app token cause event delivery conflicts,
        # so the wizard must stop before the bot starts.
        # We run this in a non-daemon thread so the bot stays alive even if
        # handler.start() on the main thread doesn't return cleanly.
        if self.on_complete:
            def _handoff():
                time.sleep(2)
                # Close wizard's socket connection
                if hasattr(self, "_handler"):
                    try:
                        self._handler.close()
                        logger.info("Wizard SocketModeHandler closed")
                    except Exception as exc:
                        logger.warning(f"Failed to close wizard handler: {exc}")
                time.sleep(3)  # Let socket close cleanly
                # Start the bot (blocking call — keeps this thread alive)
                logger.info("Starting bot from handoff thread")
                try:
                    self.on_complete(config_path, session.business_description)
                except Exception as exc:
                    logger.error(f"Bot startup failed: {exc}")
            # Non-daemon thread — survives even if main thread exits
            threading.Thread(target=_handoff, name="bot-handoff").start()

    def _send_getting_started_dm(self):
        """Send a getting-started guide as a DM to the owner after setup completes."""
        if not self.owner_slack_id:
            return

        getting_started_message = (
            "Your AI company is live! Here's how to get started:\n\n"
            "*1. Business plan forwarded*\n"
            "Your business description from setup has been automatically sent to the CEO. "
            "The team is already starting to work on it!\n\n"
            "*2. Watch your team work*\n"
            "Your CEO will break down the plan and delegate to the team. "
            "Check each agent's channel to see progress.\n\n"
            "*3. Key commands*\n"
            "\u2022 `tasks` \u2014 See the task board\n"
            "\u2022 `report` \u2014 Get a progress report\n"
            "\u2022 `standup` \u2014 Run a team standup\n"
            "\u2022 `help` \u2014 All available commands\n\n"
            "*4. Tips*\n"
            "\u2022 Be specific when talking to agents \u2014 \"Build a landing page with pricing section\" "
            "works better than \"make a website\"\n"
            "\u2022 Upload reference files (competitor sites, design mockups, specs) to give agents context\n"
            "\u2022 Your CEO reports automatically at the scheduled hours \u2014 just check #ceo\n\n"
            "You're the investor. Give direction, your AI team handles execution."
        )

        try:
            self.app.client.chat_postMessage(
                channel=self.owner_slack_id,
                text=getting_started_message,
            )
            logger.info(f"Sent getting-started guide to owner {self.owner_slack_id}")
        except Exception as exc:
            logger.error(f"Failed to send getting-started DM: {exc}")

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

        @self.app.event("app_home_opened")
        def handle_app_home_opened(event, client):
            """Show a simple home tab when user opens the app."""
            try:
                client.views_publish(
                    user_id=event["user"],
                    view={
                        "type": "home",
                        "blocks": [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": "*Welcome to Crewmatic* :rocket:\n\nYour autonomous AI team is setting up. Send me a DM to get started!",
                                },
                            },
                        ],
                    },
                )
            except Exception as exc:
                logger.warning(f"Failed to publish home tab: {exc}")

        @self.app.event("app_mention")
        def handle_mention(event, say):
            import re as _re
            user_id = event.get("user", "")
            text = _re.sub(r"<@[A-Z0-9]+>\s*", "", event.get("text", "")).strip()
            channel_id = event.get("channel", "")
            message_ts = event.get("ts", "")
            thread_ts = event.get("thread_ts", message_ts)
            files = event.get("files", [])
            self._handle_message(user_id, text, channel_id, thread_ts, say, files=files, message_ts=message_ts)

        @self.app.event("message")
        def handle_message(event, say):
            # Only handle DMs (channel type "im")
            if event.get("channel_type") != "im":
                return
            # Allow file_share subtype (user uploaded a file)
            subtype = event.get("subtype")
            if subtype and subtype != "file_share":
                return
            if event.get("bot_id"):
                return

            user_id = event.get("user", "")
            text = event.get("text", "")
            channel_id = event.get("channel", "")
            message_ts = event.get("ts", "")
            thread_ts = event.get("thread_ts", message_ts)
            files = event.get("files", [])
            self._handle_message(user_id, text, channel_id, thread_ts, say, files=files, message_ts=message_ts)

        @self.app.action("setup_confirm")
        def handle_confirm_action(ack, body):
            ack()
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            message_ts = body.get("message", {}).get("ts", "")
            thread_ts = message_ts

            session = self.sessions.get(user_id)
            if not session or session.state != SetupState.AWAITING_CONFIRMATION:
                return

            # Replace proposal card with confirmation (remove buttons)
            try:
                self.app.client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    text="Team approved! Setting up...",
                    blocks=[
                        {"type": "section", "text": {"type": "mrkdwn", "text": "*Step 4/5 — Team approved!* Setting up channels and config..."}},
                    ],
                )
            except Exception:
                pass

            def _say(**kwargs):
                self.app.client.chat_postMessage(**kwargs)

            self._handle_confirm(session, channel_id, thread_ts, _say)

        @self.app.action("setup_modify")
        def handle_modify_action(ack, body):
            ack()
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            message_ts = body.get("message", {}).get("ts", "")

            session = self.sessions.get(user_id)
            if not session:
                return

            # Replace proposal card
            try:
                self.app.client.chat_update(
                    channel=channel_id, ts=message_ts,
                    text="Making changes...",
                    blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "*Modifying team config...*"}}],
                )
            except Exception:
                pass

            session.state = SetupState.AWAITING_MODIFICATION
            self.app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
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

        @self.app.action(re.compile(r"^integration_checkboxes_\d+$"))
        def handle_integration_checkboxes(ack, body):
            # Just acknowledge — selections are read on confirm
            ack()

        @self.app.action("setup_integrations_confirm")
        def handle_integrations_confirm(ack, body):
            ack()
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            message_ts = body.get("message", {}).get("ts", "")
            thread_ts = body.get("message", {}).get("thread_ts", message_ts)

            session = self.sessions.get(user_id)
            if not session or session.state != SetupState.AWAITING_INTEGRATIONS:
                return

            # Read selected checkboxes from all checkbox blocks
            selected = []
            block_state = body.get("state", {}).get("values", {})
            for block_id, block_data in block_state.items():
                if not block_id.startswith("integration_checkboxes_block_"):
                    continue
                for action_id, action_data in block_data.items():
                    for opt in action_data.get("selected_options", []):
                        selected.append(opt["value"])

            session.selected_integrations = selected

            # Replace checkboxes with static summary
            summary = ', '.join(selected) if selected else 'none'
            try:
                self.app.client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    text=f"Integrations selected: {summary}",
                    blocks=[
                        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Step 3/5 — Integrations selected:* {summary}"}},
                    ],
                )
            except Exception:
                pass

            def _say(**kwargs):
                self.app.client.chat_postMessage(**kwargs)

            if selected:
                _say(
                    text=f"Great, enabling integrations: {', '.join(selected)}",
                    channel=channel_id,
                    thread_ts=thread_ts,
                )
                # Start credential collection for selected integrations
                self._start_credential_collection(session, channel_id, thread_ts, _say)
            else:
                self._generate_and_show_proposal(session, channel_id, thread_ts, _say)

        @self.app.action("setup_skip_integrations")
        def handle_skip_integrations(ack, body):
            ack()
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            message_ts = body.get("message", {}).get("ts", "")
            thread_ts = body.get("message", {}).get("thread_ts", message_ts)

            session = self.sessions.get(user_id)
            if not session or session.state != SetupState.AWAITING_INTEGRATIONS:
                return

            session.selected_integrations = []

            # Replace checkboxes with static text
            try:
                self.app.client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    text="Integrations skipped",
                    blocks=[
                        {"type": "section", "text": {"type": "mrkdwn", "text": "*Step 3/5 — Integrations:* skipped"}},
                    ],
                )
            except Exception:
                pass

            def _say(**kwargs):
                self.app.client.chat_postMessage(**kwargs)

            self._generate_and_show_proposal(session, channel_id, thread_ts, _say)

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
        self._handler = SocketModeHandler(self.app, self.app_token)
        self._handler.start()  # Blocks — bot handoff runs on a separate thread
