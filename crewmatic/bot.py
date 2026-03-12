"""Crewmatic Slack bot — core orchestration engine."""

import logging
import os
import re
import threading
import time
from datetime import datetime

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from .agent_loader import AgentConfig, load_agents, get_leader
from .claude_runner import ClaudeRunner
from .config import load_config
from .context import build_prompt
from .delegation import handle_delegations as _handle_delegations
from .project_manager import ProjectManager
from .scheduler import Scheduler
from .task_manager import TaskManager

logger = logging.getLogger(__name__)


class CrewmaticBot:
    """The main Crewmatic bot — wires everything together."""

    def __init__(self, config_path: str | None = None):
        load_dotenv()

        self.config = load_config(config_path)
        self.agents = load_agents(self.config)
        self.settings = self.config["settings"]

        # Core components
        self.task_manager = TaskManager(
            data_dir=self.config["data_dir"],
            stuck_timeout_minutes=self.settings["stuck_timeout_minutes"],
            archive_after_days=self.settings["archive_after_days"],
        )

        projects_config = self.config.get("projects", {})
        self.project_manager = ProjectManager(
            projects_config=projects_config,
            data_dir=self.config["data_dir"],
        )

        self.claude = ClaudeRunner(
            max_concurrent=self.settings["max_concurrent_agents"],
            timeout=self.settings["claude_timeout"],
            cwd=self.config.get("_config_dir", os.getcwd()),
        )

        # Slack setup
        slack_config = self.config.get("slack", {})
        bot_token = slack_config.get("bot_token") or os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = slack_config.get("app_token") or os.environ.get("SLACK_APP_TOKEN", "")

        self.app = App(token=bot_token)
        self.app_token = app_token

        # Per-agent Slack clients
        self.agent_clients: dict[str, WebClient] = {}
        for agent_name in self.agents:
            env_key = f"SLACK_BOT_TOKEN_{agent_name.upper()}"
            token = os.environ.get(env_key)
            if token:
                self.agent_clients[agent_name] = WebClient(token=token)
                logger.info(f"Loaded dedicated Slack bot for {agent_name.upper()}")

        # Bot identity tracking
        self.bot_user_id: str | None = None
        self.all_bot_user_ids: set[str] = set()
        self.bot_user_id_to_agent: dict[str, str] = {}

        # Channel mappings
        self.channel_name_to_id: dict[str, str] = {}
        self.channel_id_to_name: dict[str, str] = {}

        # Loop prevention
        self.recent_bot_messages: dict[str, float] = {}
        self._bot_msg_lock = threading.Lock()
        self.loop_cooldown = self.settings.get("loop_cooldown", 30)

        # Owner config
        owner = self.config.get("owner", {})
        self.owner_slack_id = owner.get("slack_id", "")

        # Scheduler
        self.scheduler = Scheduler(
            agents=self.agents,
            config=self.config,
            task_manager=self.task_manager,
            project_manager=self.project_manager,
            call_agent_fn=self.call_agent,
            post_fn=self.post_to_channel,
            handle_delegations_fn=self._handle_delegations,
        )

        # Register Slack event handlers
        self._register_handlers()

    def build_channel_map(self):
        """Build channel name/ID mappings and discover bot user IDs."""
        try:
            result = self.app.client.conversations_list(types="public_channel")
            for ch in result["channels"]:
                self.channel_name_to_id[ch["name"]] = ch["id"]
                self.channel_id_to_name[ch["id"]] = ch["name"]
            logger.info(f"Channel map: {list(self.channel_name_to_id.keys())}")
        except Exception as e:
            logger.error(f"Failed to build channel map: {e}")

        try:
            auth = self.app.client.auth_test()
            self.bot_user_id = auth["user_id"]
            self.all_bot_user_ids.add(self.bot_user_id)
            self.bot_user_id_to_agent[self.bot_user_id] = "crewmatic"
        except Exception as e:
            logger.error(f"Failed to get bot user ID: {e}")

        for agent_name, client in self.agent_clients.items():
            try:
                auth = client.auth_test()
                self.all_bot_user_ids.add(auth["user_id"])
                self.bot_user_id_to_agent[auth["user_id"]] = agent_name.upper()
            except Exception as e:
                logger.error(f"Failed to get bot user ID for {agent_name}: {e}")

    def get_channel_name(self, channel_id: str) -> str | None:
        if channel_id in self.channel_id_to_name:
            return self.channel_id_to_name[channel_id]
        try:
            result = self.app.client.conversations_info(channel=channel_id)
            name = result["channel"]["name"]
            self.channel_id_to_name[channel_id] = name
            self.channel_name_to_id[name] = channel_id
            return name
        except Exception:
            return None

    def get_agent_client(self, agent_name: str | None = None) -> WebClient:
        if agent_name and agent_name in self.agent_clients:
            return self.agent_clients[agent_name]
        return self.app.client

    def post_to_channel(self, channel_name: str, text: str, thread_ts: str | None = None, agent_name: str | None = None):
        channel_id = self.channel_name_to_id.get(channel_name)
        if not channel_id:
            logger.error(f"Channel #{channel_name} not found")
            return
        max_len = self.settings.get("slack_max_length", 39000)
        if len(text) > max_len:
            text = text[:max_len] + "\n\n... (truncated)"
        kwargs = {"channel": channel_id, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        try:
            client = self.get_agent_client(agent_name)
            client.chat_postMessage(**kwargs)
            with self._bot_msg_lock:
                self.recent_bot_messages[channel_id] = time.time()
        except Exception as e:
            logger.error(f"Failed to post to #{channel_name}: {e}")

    def resolve_agent(self, channel_name: str | None, text: str) -> tuple[str | None, AgentConfig | None]:
        """Resolve which agent should handle a message."""
        if channel_name:
            for name, agent in self.agents.items():
                if channel_name == agent.channel:
                    return name, agent

        text_lower = text.lower().strip()
        for name, agent in self.agents.items():
            if text_lower.startswith(f"{name}:") or text_lower.startswith(f"{name} "):
                return name, agent

        return None, None

    def call_agent(self, agent_name: str, message: str, context: str = "") -> str:
        """Call a specific agent with full context injection."""
        agent = self.agents.get(agent_name)
        if not agent:
            return f"Agent {agent_name} doesn't exist."

        if context:
            message = f"Previous conversation context:\n{context}\n\nNew message:\n{message}"

        # Build full prompt with context injection
        active_project = self.project_manager.get_active_project()
        project_ctx = self.project_manager.get_project_context()
        saved_ctx = ""
        if agent.role == "leader" and active_project:
            saved_ctx = self.project_manager.load_project_context_file(active_project)

        full_prompt = build_prompt(
            agent_name=agent_name,
            message=message,
            receives_context=agent.receives_context,
            memory_dir=self.config["memory_dir"],
            context_dir=self.config["context_dir"],
            task_summary=self.task_manager.get_summary(),
            client=self.app.client,
            channel_name_to_id=self.channel_name_to_id,
            project_context=project_ctx,
            saved_context=saved_ctx,
            owner_channel=agent.channel,
            cache_ttl=self.settings.get("cache_ttl", 300),
        )

        # Determine cwd — use project codebase if available
        cwd = self.project_manager.get_project_codebase() or self.config.get("_config_dir")

        # Git identity from config
        env_overrides = {}
        git_config = self.config.get("git", {})
        if git_config.get("author_name"):
            env_overrides["GIT_AUTHOR_NAME"] = git_config["author_name"]
            env_overrides["GIT_COMMITTER_NAME"] = git_config["author_name"]
        if git_config.get("author_email"):
            env_overrides["GIT_AUTHOR_EMAIL"] = git_config["author_email"]
            env_overrides["GIT_COMMITTER_EMAIL"] = git_config["author_email"]

        return self.claude.call(
            system_prompt=agent.system_prompt,
            user_message=full_prompt,
            model=agent.model,
            allowed_tools=agent.tools,
            cwd=cwd,
            env_overrides=env_overrides if env_overrides else None,
        )

    def _handle_delegations(self, source_agent: str, response: str):
        agent_names = set(self.agents.keys())
        _handle_delegations(
            source_agent=source_agent,
            response=response,
            agent_names=agent_names,
            add_task_fn=self.task_manager.add_task,
        )

    def _auto_save_leader_context(self, project_key: str):
        """Ask leader to dump working context before switching projects."""
        leader = get_leader(self.agents)
        if not leader:
            return
        try:
            tasks_summary = self.task_manager.get_summary(include_done=True)
            prompt = (
                f"We are pausing project *{project_key}*. Write a concise context dump (max 500 words) "
                f"that future-you can read to instantly resume work. Include:\n"
                f"- What was being worked on right now\n"
                f"- What's in progress / blocked\n"
                f"- Key decisions made recently\n"
                f"- Next steps when we resume\n"
                f"Write it as bullet points. This is for YOUR memory, not for Slack."
            )
            response = self.call_agent(leader.name, prompt)
            self.project_manager.save_project_context(project_key, response)
            logger.info(f"Leader context saved for {project_key}")
        except Exception as e:
            logger.error(f"Failed to save leader context: {e}")

    def get_thread_context(self, channel: str, thread_ts: str, limit: int = 10) -> str:
        try:
            result = self.app.client.conversations_replies(channel=channel, ts=thread_ts, limit=limit)
            messages = result.get("messages", [])
            parts = []
            for msg in messages[:-1]:
                text = msg.get("text", "")
                user_id = msg.get("user", "")
                if user_id in self.bot_user_id_to_agent:
                    speaker = self.bot_user_id_to_agent[user_id]
                elif user_id in self.all_bot_user_ids:
                    speaker = "Agent"
                else:
                    speaker = "User"
                parts.append(f"{speaker}: {text}")
            return "\n".join(parts)
        except Exception:
            return ""

    def handle_command(self, text: str, channel_name: str) -> str | None:
        """Handle built-in slash commands."""
        text_lower = text.lower().strip()

        if text_lower == "standup":
            threading.Thread(target=self.scheduler.run_standup, daemon=True).start()
            return "Running team standup..."

        if text_lower == "report":
            threading.Thread(target=self.scheduler.run_report, daemon=True).start()
            return "Running report..."

        if text_lower == "tasks":
            return f"Task Board\n\n{self.task_manager.get_summary(include_done=True)}"

        if text_lower == "my tasks":
            for name, agent in self.agents.items():
                if channel_name == agent.channel:
                    tasks = self.task_manager.get_tasks(assigned_to=name, status="todo")
                    if not tasks:
                        return f"No open tasks for {name.upper()}."
                    lines = [f"#{t['id']} [{t['priority'].upper()}] {t['title']}" for t in tasks]
                    return f"Tasks for {name.upper()}:\n" + "\n".join(lines)
            return "This channel has no assigned agent."

        if text_lower.startswith("cancel"):
            parts = text.strip().split(None, 2)
            if len(parts) >= 2:
                task_id_str = parts[1].lstrip("#")
                try:
                    task_id = int(task_id_str)
                    reason = parts[2] if len(parts) > 2 else ""
                    result = self.task_manager.cancel_task(task_id, reason)
                    if result:
                        return f"Task #{task_id} cancelled."
                    return f"Task #{task_id} not found or already completed."
                except ValueError:
                    return "Usage: cancel #42 reason"
            return "Usage: cancel #42 reason"

        if text_lower.startswith("start "):
            project_key = text_lower.split(None, 1)[1].strip()
            old_project = self.project_manager.get_active_project()
            if old_project and old_project != project_key:
                self._auto_save_leader_context(old_project)
            if self.project_manager.start_project(project_key):
                saved_ctx = self.project_manager.load_project_context_file(project_key)
                ctx_msg = f" Loaded saved context ({len(saved_ctx)} chars)." if saved_ctx else ""
                return f"Started project: {project_key}.{ctx_msg}"
            return f"Unknown project: {project_key}. Use 'projects' to see available ones."

        if text_lower == "stop":
            old_project = self.project_manager.get_active_project()
            if old_project:
                self._auto_save_leader_context(old_project)
            prev = self.project_manager.stop_project()
            if prev:
                return f"Stopped project {prev}. Context saved."
            return "Already idle."

        if text_lower == "status":
            return self.project_manager.get_status()

        if text_lower == "projects":
            return self.project_manager.list_projects()

        if text_lower == "help":
            return (
                "Available commands:\n"
                "  standup — Run team standup\n"
                "  report — Run progress report\n"
                "  tasks — Show all tasks\n"
                "  my tasks — Show tasks for this channel's agent\n"
                "  cancel #42 reason — Cancel a task\n"
                "  start <project> — Start a project\n"
                "  stop — Stop current project\n"
                "  status — Show current project status\n"
                "  projects — List available projects\n"
                "  help — Show this help"
            )

        return None

    def _handle_agent_reply(self, agent_name: str, text: str, channel_id: str, thread_ts: str, context: str = ""):
        """Run agent call in background thread."""
        try:
            response = self.call_agent(agent_name, text, context)
            max_len = self.settings.get("slack_max_length", 39000)
            if len(response) > max_len:
                response = response[:max_len] + "\n\n... (truncated)"
            kwargs = {"channel": channel_id, "text": response}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            client = self.get_agent_client(agent_name)
            client.chat_postMessage(**kwargs)
            with self._bot_msg_lock:
                self.recent_bot_messages[channel_id] = time.time()
            self._handle_delegations(agent_name, response)
        except Exception as e:
            logger.error(f"Agent reply error ({agent_name}): {e}")

    def _register_handlers(self):
        """Register Slack event handlers."""

        @self.app.event("app_mention")
        def handle_mention(event, say):
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", event.get("text", "")).strip()
            channel_id = event.get("channel")
            thread_ts = event.get("thread_ts", event.get("ts"))
            channel_name = self.get_channel_name(channel_id)

            agent_name, agent = self.resolve_agent(channel_name, text)
            if not agent:
                leader = get_leader(self.agents)
                agent_name = leader.name if leader else list(self.agents.keys())[0]

            cmd_response = self.handle_command(text, channel_name)
            if cmd_response:
                client = self.get_agent_client(agent_name)
                client.chat_postMessage(channel=channel_id, text=cmd_response, thread_ts=thread_ts)
                return

            logger.info(f"Routing to {agent_name} in #{channel_name}: {text[:80]}")

            context = ""
            if event.get("thread_ts"):
                context = self.get_thread_context(channel_id, thread_ts)

            user_id = event.get("user", "")
            if user_id == self.owner_slack_id:
                try:
                    client = self.get_agent_client(agent_name)
                    client.chat_postMessage(
                        channel=channel_id, thread_ts=thread_ts,
                        text=f"{agent_name.upper()} is on it.",
                    )
                except Exception:
                    pass

            threading.Thread(
                target=self._handle_agent_reply,
                args=(agent_name, text, channel_id, thread_ts, context),
                daemon=True,
            ).start()

        @self.app.event("message")
        def handle_message(event, say):
            if event.get("subtype"):
                return
            if event.get("user") in self.all_bot_user_ids:
                return
            if event.get("bot_id"):
                return

            text = event.get("text", "")
            channel_id = event.get("channel")
            thread_ts = event.get("thread_ts", event.get("ts"))
            channel_name = self.get_channel_name(channel_id)

            agent_name, agent = self.resolve_agent(channel_name, text)
            if not agent:
                return

            cmd_response = self.handle_command(text, channel_name)
            if cmd_response:
                client = self.get_agent_client(agent_name)
                client.chat_postMessage(channel=channel_id, text=cmd_response, thread_ts=thread_ts)
                return

            logger.info(f"Message to {agent_name} in #{channel_name}: {text[:80]}")

            context = ""
            if event.get("thread_ts"):
                context = self.get_thread_context(channel_id, thread_ts)

            user_id = event.get("user", "")
            if user_id == self.owner_slack_id:
                try:
                    client = self.get_agent_client(agent_name)
                    client.chat_postMessage(
                        channel=channel_id, thread_ts=thread_ts,
                        text=f"{agent_name.upper()} is on it.",
                    )
                except Exception:
                    pass

            threading.Thread(
                target=self._handle_agent_reply,
                args=(agent_name, text, channel_id, thread_ts, context),
                daemon=True,
            ).start()

        @self.app.event("app_home_opened")
        def handle_app_home(event, client):
            tasks_text = self.task_manager.get_summary()
            report_hours = self.settings.get("report_hours", [])
            schedule_text = " | ".join(f"{h}:00" for h in report_hours)
            client.views_publish(
                user_id=event["user"],
                view={
                    "type": "home",
                    "blocks": [
                        {"type": "header", "text": {"type": "plain_text", "text": f"{self.config.get('name', 'Crewmatic')} Dashboard"}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Active agents:* {', '.join(a.upper() for a in self.agents.keys())}"}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": "*Channels:*\n" + "\n".join(f"• #{a.channel} → {n.upper()}" for n, a in self.agents.items())}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Report schedule:* {schedule_text}"}},
                        {"type": "divider"},
                        {"type": "header", "text": {"type": "plain_text", "text": "Task Board"}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": tasks_text}},
                        {"type": "divider"},
                        {"type": "section", "text": {"type": "mrkdwn", "text": "*Commands:* `standup` | `report` | `tasks` | `my tasks` | `help`"}},
                    ],
                },
            )

    def start(self):
        """Start the bot with all loops."""
        logger.info(f"Starting Crewmatic: {self.config.get('name', 'unnamed')}")
        logger.info(f"Agents: {list(self.agents.keys())}")

        self.build_channel_map()

        # Ensure data directories exist
        os.makedirs(self.config["memory_dir"], exist_ok=True)
        os.makedirs(self.config["context_dir"], exist_ok=True)

        # Archive old tasks
        archived = self.task_manager.archive_old_tasks()
        if archived:
            logger.info(f"Archived {archived} old tasks on startup")

        # Start planning loop
        threading.Thread(target=self.scheduler.planning_loop, daemon=True, name="planner").start()
        logger.info("Planning loop started")

        # Start worker loops
        for agent_name in self.agents:
            threading.Thread(
                target=self.scheduler.agent_work_loop,
                args=(agent_name,),
                daemon=True,
                name=f"worker-{agent_name}",
            ).start()
            logger.info(f"Worker loop started for {agent_name.upper()}")

        # Start report scheduler
        threading.Thread(target=self.scheduler.report_loop, daemon=True, name="reporter").start()
        logger.info("Report scheduler started")

        # Start Slack Socket Mode
        handler = SocketModeHandler(self.app, self.app_token)
        handler.start()
