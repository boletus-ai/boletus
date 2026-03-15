"""Local terminal REPL — run boletus without Slack."""

import logging
import os
import re
import sys

from dotenv import load_dotenv

from .agent_loader import AgentConfig, load_agents, get_leader
from .claude_runner import ClaudeRunner, BoletusError
from .config import load_config
from .context import build_prompt
from .delegation import handle_delegations as _handle_delegations
from .project_manager import ProjectManager
from .task_manager import TaskManager

logger = logging.getLogger(__name__)

HELP_TEXT = """\
Available commands:
  @<agent>: <message>  — Send a message to an agent
  tasks                — Show task board
  status               — Show current project status
  projects             — List available projects
  start <project>      — Start a project
  stop                 — Stop current project
  agents               — List configured agents
  help                 — Show this help
  quit / exit          — Exit the REPL"""


class LocalRunner:
    """Interactive terminal REPL for boletus — no Slack required."""

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
            skip_permissions=self.settings.get("skip_permissions", True),
        )

        # Ensure data directories exist
        os.makedirs(self.config["memory_dir"], exist_ok=True)
        os.makedirs(self.config["context_dir"], exist_ok=True)

    def call_agent(self, agent_name: str, message: str) -> str:
        """Call a specific agent with full context injection."""
        agent = self.agents.get(agent_name)
        if not agent:
            return f"Agent {agent_name} doesn't exist."

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
            client=None,
            channel_name_to_id=None,
            project_context=project_ctx,
            saved_context=saved_ctx,
            owner_channel=agent.channel,
            cache_ttl=self.settings.get("cache_ttl", 300),
            data_dir=self.config.get("data_dir", ""),
            codebase_path=self.project_manager.get_project_codebase() or "",
        )

        cwd = self.project_manager.get_project_codebase() or self.config.get("_config_dir")

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
        """Parse delegations from response and add to task board."""
        agent_names = set(self.agents.keys())
        existing_tasks = self.task_manager.get_tasks()
        _handle_delegations(
            source_agent=source_agent,
            response=response,
            agent_names=agent_names,
            add_task_fn=self.task_manager.add_task,
            existing_tasks=existing_tasks,
        )

    def resolve_agent(self, text: str) -> tuple[str | None, str]:
        """Parse @agent: message from user input.

        Returns:
            (agent_name, message) — agent_name is None if no match.
        """
        match = re.match(r"@(\w+)[:\s]+(.+)", text, re.DOTALL)
        if match:
            name = match.group(1).lower()
            message = match.group(2).strip()
            if name in self.agents:
                return name, message
        return None, text

    def handle_command(self, text: str) -> str | None:
        """Handle built-in REPL commands. Returns response or None."""
        text_lower = text.lower().strip()

        if text_lower == "tasks":
            return f"Task Board\n\n{self.task_manager.get_summary(include_done=True)}"

        if text_lower == "status":
            return self.project_manager.get_status()

        if text_lower == "projects":
            return self.project_manager.list_projects()

        if text_lower.startswith("start "):
            project_key = text_lower.split(None, 1)[1].strip()
            if self.project_manager.start_project(project_key):
                saved_ctx = self.project_manager.load_project_context_file(project_key)
                ctx_msg = f" Loaded saved context ({len(saved_ctx)} chars)." if saved_ctx else ""
                return f"Started project: {project_key}.{ctx_msg}"
            return f"Unknown project: {project_key}. Use 'projects' to see available ones."

        if text_lower == "stop":
            prev = self.project_manager.stop_project()
            if prev:
                return f"Stopped project {prev}."
            return "Already idle."

        if text_lower == "agents":
            lines = []
            for name, agent in self.agents.items():
                delegates = ", ".join(agent.delegates_to) if agent.delegates_to else "none"
                lines.append(f"  {name} (role={agent.role}, model={agent.model}, delegates_to={delegates})")
            return "Agents:\n" + "\n".join(lines)

        if text_lower == "help":
            return HELP_TEXT

        return None

    def run(self):
        """Start the interactive REPL loop."""
        name = self.config.get("name", "boletus")
        print(f"{name} — local mode")
        print(f"Agents: {', '.join(self.agents.keys())}")
        print("Type 'help' for commands, '@agent: message' to talk to an agent.\n")

        while True:
            try:
                text = input("boletus> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not text:
                continue

            if text.lower() in ("quit", "exit"):
                print("Bye.")
                break

            # Built-in commands
            cmd_response = self.handle_command(text)
            if cmd_response is not None:
                print(cmd_response)
                continue

            # Agent message: @agent: message
            agent_name, message = self.resolve_agent(text)
            if not agent_name:
                # Default to leader if no @agent prefix
                leader = get_leader(self.agents)
                if leader:
                    agent_name = leader.name
                    message = text
                else:
                    print("No agent matched. Use @<agent>: <message> or type 'help'.")
                    continue

            print(f"[{agent_name.upper()}] thinking...")
            try:
                response = self.call_agent(agent_name, message)
                print(f"\n[{agent_name.upper()}]\n{response}\n")
                self._handle_delegations(agent_name, response)
            except BoletusError as e:
                print(f"[ERROR] {e}\n")
            except Exception as e:
                logger.error(f"Agent call failed: {e}")
                print(f"[ERROR] {e}\n")


def start_local(config_path: str | None = None):
    """Entry point for the local runner."""
    runner = LocalRunner(config_path=config_path)
    runner.run()
