"""Crewmatic Slack bot — core orchestration engine."""

import json
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

from .agent_loader import AgentConfig, load_agents, get_leader, get_effective_channel
from .claude_runner import ClaudeRunner
from .config import load_config
from .cost_tracker import CostTracker
from .llm import CrewmaticError
from .context import build_prompt
from .delegation import handle_delegations as _handle_delegations
from .guardrails import CircuitBreaker, CircuitBrokenError, ExecutionGuard
from .slack_format import markdown_to_slack
from .integrations import resolve_integrations_for_agent, build_mcp_config_for_integrations, get_agent_integration_instructions, get_claude_ai_tools_for_integrations
from .link_tracker import LinkTracker
from .project_manager import ProjectManager
from .scheduler import Scheduler
from .task_manager import TaskManager
from .workflows import WorkflowEngine

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

        llm_backend = self.settings.get("llm_backend", "cli")
        if llm_backend == "api":
            from .llm import AnthropicAPIRunner
            self.claude = AnthropicAPIRunner(
                max_concurrent=self.settings["max_concurrent_agents"],
                timeout=self.settings["claude_timeout"],
            )
            logger.info("Using Anthropic API backend")
        else:
            self.claude = ClaudeRunner(
                max_concurrent=self.settings["max_concurrent_agents"],
                timeout=self.settings["claude_timeout"],
                cwd=self.config.get("_config_dir", os.getcwd()),
                skip_permissions=self.settings.get("skip_permissions", True),
            )
            logger.info("Using Claude CLI backend")

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

        # Event deduplication
        self._seen_event_ts: dict[str, bool] = {}

        # Owner config
        owner = self.config.get("owner", {})
        self.owner_slack_id = owner.get("slack_id", "")

        # Safety guardrails
        cb = CircuitBreaker(
            max_failures=self.settings.get("max_consecutive_failures", 3),
            reset_after=self.settings.get("circuit_reset_minutes", 10) * 60,
        )
        self.guardrails = ExecutionGuard(cb, agent_names=set(self.agents.keys()))

        # Cost tracking
        self.cost_tracker = CostTracker(data_dir=self.config["data_dir"])

        # Link tracking
        self.link_tracker = LinkTracker(data_dir=self.config["data_dir"])

        # Workflow engine
        self.workflow_engine = WorkflowEngine(
            config=self.config,
            call_agent_fn=self.call_agent,
            post_fn=self.post_to_channel,
            task_manager=self.task_manager,
        )

        # Scheduler
        self.scheduler = Scheduler(
            agents=self.agents,
            config=self.config,
            task_manager=self.task_manager,
            project_manager=self.project_manager,
            call_agent_fn=self.call_agent,
            post_fn=self.post_to_channel,
            handle_delegations_fn=self._handle_delegations,
            guardrails=self.guardrails,
            cost_summary_fn=self.cost_tracker.get_summary,
        )

        # Register Slack event handlers
        self._register_handlers()

    def build_channel_map(self):
        """Build channel name/ID mappings and discover bot user IDs."""
        try:
            cursor = None
            while True:
                kwargs = {"types": "public_channel", "limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor
                result = self.app.client.conversations_list(**kwargs)
                for ch in result["channels"]:
                    self.channel_name_to_id[ch["name"]] = ch["id"]
                    self.channel_id_to_name[ch["id"]] = ch["name"]
                cursor = result.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            logger.info(f"Channel map: {len(self.channel_name_to_id)} channels")
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
        # Convert GitHub-flavored markdown to Slack mrkdwn
        text = markdown_to_slack(text)
        # Prepend agent identity for shared channels
        if agent_name:
            text = f"*{agent_name.upper()}*: {text}"
        if len(text) > max_len:
            text = text[:max_len] + "\n\n... (truncated)"
        kwargs = {"channel": channel_id, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        try:
            client = self.get_agent_client(agent_name)
            client.chat_postMessage(**kwargs)
        except Exception as e:
            logger.error(f"Failed to post to #{channel_name}: {e}")

    def resolve_agent(self, channel_name: str | None, text: str) -> tuple[str | None, AgentConfig | None]:
        """Resolve which agent should handle a message.

        With shared channels, checks text prefix first (@agent or agent:),
        then falls back to the channel owner (leader/manager).
        """
        text_lower = text.lower().strip()

        # Check text prefix first — works in shared channels
        for name, agent in self.agents.items():
            if (
                text_lower.startswith(f"@{name}:")
                or text_lower.startswith(f"@{name} ")
                or text_lower.startswith(f"{name}:")
                or text_lower.startswith(f"{name} ")
            ):
                return name, agent

        # Fallback: channel owner (leader or manager assigned to this channel)
        if channel_name:
            # Prefer leader/manager who owns the channel
            for name, agent in self.agents.items():
                if agent.channel == channel_name and agent.role in ("leader", "manager"):
                    return name, agent
            # Last resort: any agent on this channel
            for name, agent in self.agents.items():
                if get_effective_channel(name, self.agents) == channel_name:
                    return name, agent

        return None, None

    def _build_mcp_config(self, agent: AgentConfig) -> str | None:
        """Generate MCP config JSON for an agent.

        Merges three sources:
        1. Built-in integrations (postgres MCP from catalog)
        2. Custom MCP servers from crew.yaml mcp_servers: section
        3. Per-agent MCP servers from agent config

        Returns path to the config file, or None if no MCP servers.
        """
        global_integrations = self.config.get("integrations", [])
        agent_integrations = resolve_integrations_for_agent(
            agent.role, agent.integrations, global_integrations
        )

        mcp_config = build_mcp_config_for_integrations(agent_integrations) if agent_integrations else {"mcpServers": {}}

        # Merge custom MCP servers from crew.yaml (global)
        # Resolve ${ENV_VAR} placeholders in args and env values
        custom_servers = self.config.get("mcp_servers", {})
        for name, server in custom_servers.items():
            if name not in mcp_config["mcpServers"]:
                resolved = dict(server)
                if "args" in resolved:
                    resolved_args = []
                    for arg in resolved["args"]:
                        if isinstance(arg, str) and arg.startswith("${") and arg.endswith("}"):
                            val = os.environ.get(arg[2:-1], "")
                            if val:
                                resolved_args.append(val)
                            else:
                                logger.warning(f"Custom MCP '{name}': env var {arg[2:-1]} not set, skipping arg")
                        else:
                            resolved_args.append(arg)
                    resolved["args"] = resolved_args
                if "env" in resolved:
                    resolved_env = {}
                    for k, v in resolved["env"].items():
                        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                            resolved_env[k] = os.environ.get(v[2:-1], v)
                        else:
                            resolved_env[k] = v
                    resolved["env"] = resolved_env
                mcp_config["mcpServers"][name] = resolved

        if not mcp_config.get("mcpServers"):
            return None

        # Write config file
        config_dir = os.path.join(self.config["data_dir"], "mcp_configs")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, f"{agent.name}.json")

        with open(config_path, "w") as f:
            json.dump(mcp_config, f, indent=2)

        return config_path

    def call_agent(self, agent_name: str, message: str, context: str = "") -> str:
        """Call a specific agent with full context injection."""
        agent = self.agents.get(agent_name)
        if not agent:
            return f"Agent {agent_name} doesn't exist."

        # Safety guard — check circuit breaker before calling
        allowed, reason = self.guardrails.can_execute(agent_name)
        if not allowed:
            logger.warning(f"Guard blocked {agent_name}: {reason}")
            raise CircuitBrokenError(agent_name, reason)

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
            task_summary=self.task_manager.get_summary(include_done=agent.role == "leader"),
            client=self.app.client,
            channel_name_to_id=self.channel_name_to_id,
            project_context=project_ctx,
            saved_context=saved_ctx,
            owner_channel=None,  # Shared channels — don't exclude team messages
            cache_ttl=self.settings.get("cache_ttl", 300),
            data_dir=self.config["data_dir"],
            codebase_path=self.project_manager.get_project_codebase() or "",
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

        # MCP server config for this agent
        mcp_config = self._build_mcp_config(agent)

        # Inject integration CLI instructions into system prompt
        global_integrations = self.config.get("integrations", [])
        agent_integrations = resolve_integrations_for_agent(
            agent.role, agent.integrations, global_integrations
        )
        integration_instructions = get_agent_integration_instructions(agent_integrations)
        system_prompt = agent.system_prompt
        if integration_instructions:
            system_prompt += "\n" + integration_instructions

        # Email safety guardrail
        email_mode = self.settings.get("email_mode", "drafts")
        if "gmail" in agent_integrations and email_mode == "drafts":
            system_prompt += (
                "\n\nEMAIL POLICY: You may ONLY create email drafts (gmail_create_draft). "
                "NEVER send emails directly. The owner reviews and sends all emails manually. "
                "This is a safety policy — do not bypass it."
            )

        # Output format guardrails
        system_prompt += (
            "\n\nOUTPUT RULES:"
            "\n- You are posting to Slack. Use Slack mrkdwn formatting:"
            "\n  Bold: *text* (single asterisk). Italic: _text_ (single underscore)."
            "\n  Do NOT use ## headings, **double asterisks**, or markdown table syntax."
            "\n- NEVER generate or invent URLs to external services. If you used a tool to "
            "create something (Notion page, Canva design, etc.), share the REAL URL from "
            "the tool response. Never make up URLs."
            "\n- Keep messages concise. Use bullet points over long paragraphs."
        )

        # If agent has Notion, inject project name for organized page hierarchy
        if "notion" in agent_integrations:
            project_name = self.config.get("name", "")
            if project_name:
                system_prompt += (
                    f"\n\nNOTION WORKSPACE: All Notion content must go under a top-level "
                    f"page called '{project_name}'. Search for it first (notion-search). "
                    f"If it doesn't exist, create it. Every doc, plan, report = sub-page "
                    f"under '{project_name}'. Never create orphaned pages."
                )

        # Append Claude.ai MCP tool patterns to allowed_tools
        # Always ensure agents have tools — without --allowedTools the subprocess
        # prompts interactively and hangs in headless mode
        default_tools = "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch"
        allowed_tools = agent.tools or default_tools
        claude_ai_patterns = get_claude_ai_tools_for_integrations(agent_integrations)
        if claude_ai_patterns:
            allowed_tools = allowed_tools + "," + ",".join(claude_ai_patterns)

        try:
            result = self.claude.call(
                system_prompt=system_prompt,
                user_message=full_prompt,
                model=agent.model,
                allowed_tools=allowed_tools,
                cwd=cwd,
                env_overrides=env_overrides if env_overrides else None,
                mcp_config=mcp_config,
            )
            self.guardrails.circuit_breaker.record_success(agent_name)
            self.cost_tracker.record_call(agent_name, agent.model)
            self.link_tracker.extract_and_save(agent_name, result)
            return result
        except Exception:
            tripped = self.guardrails.circuit_breaker.record_failure(agent_name)
            if tripped:
                raise CircuitBrokenError(agent_name, "consecutive failures exceeded threshold")
            raise

    def _handle_delegations(self, source_agent: str, response: str):
        agent_names = set(self.agents.keys())
        existing_tasks = self.task_manager.get_tasks()
        prev_count = len([t for t in existing_tasks if t.get("status") in ("todo", "in_progress")])
        unknown_delegations = _handle_delegations(
            source_agent=source_agent,
            response=response,
            agent_names=agent_names,
            add_task_fn=self.task_manager.add_task,
            existing_tasks=existing_tasks,
        )

        # Auto-hire: if CEO/CTO delegated to agents that don't exist, create them
        if unknown_delegations:
            source_obj = self.agents.get(source_agent)
            if source_obj and source_obj.role in ("leader", "manager"):
                for new_name, first_task in unknown_delegations:
                    threading.Thread(
                        target=self._auto_hire_agent,
                        args=(source_agent, new_name, first_task),
                        daemon=True,
                    ).start()

        # Auto-activate: if leader just created first tasks, ensure system is running
        new_count = self.task_manager.count_open_tasks()
        if prev_count == 0 and new_count > 0 and not self.project_manager.is_active():
            source_agent_obj = self.agents.get(source_agent)
            if source_agent_obj and source_agent_obj.role == "leader":
                self._auto_create_project(source_agent, response)

    def _auto_hire_agent(self, hiring_manager: str, new_agent_name: str, first_task: str):
        """Auto-create a new agent when a leader/manager delegates to one that doesn't exist.

        The hiring manager's role determines the new agent's relationship:
        - Leader hires → new agent reports_to leader, role=worker (or manager if complex)
        - Manager hires → new agent reports_to manager, role=worker
        """
        manager = self.agents.get(hiring_manager)
        if not manager:
            return

        # Prevent hiring duplicates
        if new_agent_name in self.agents:
            return

        logger.info(f"Auto-hiring: {hiring_manager} wants to create agent '{new_agent_name}' for: {first_task[:80]}")

        self.post_to_channel(
            manager.channel,
            f"Hiring *{new_agent_name.upper()}* for the team...",
            agent_name=hiring_manager,
        )

        try:
            request = (
                f"The {hiring_manager.upper()} ({manager.role}) needs a new team member called '{new_agent_name}'.\n"
                f"Their first task will be: {first_task}\n\n"
                f"The new agent should:\n"
                f"- report_to: {hiring_manager}\n"
                f"- role: worker\n"
                f"- Have a system prompt tailored to this kind of work\n"
                f"- Channel name: {manager.channel} (shares manager's channel)\n"
                f"- delegates_to should be empty (worker)\n"
            )

            # Track agents before hire to detect the actual name LLM chose
            agents_before = set(self.agents.keys())
            self._handle_add_agent(request, manager.channel)
            agents_after = set(self.agents.keys())
            actually_created = agents_after - agents_before

            # Use the actual agent name (LLM might generate "backend_developer" instead of "backend_dev")
            actual_name = next(iter(actually_created), None) or new_agent_name

            if actual_name in self.agents:
                self.task_manager.add_task(
                    first_task,
                    assigned_to=actual_name,
                    created_by=hiring_manager,
                )
                logger.info(f"Auto-hire complete: {actual_name} created with first task")

                # Update the hiring manager's delegates_to
                if actual_name not in manager.delegates_to:
                    manager.delegates_to.append(actual_name)
                    logger.info(f"Added {actual_name} to {hiring_manager}'s delegates_to")

        except Exception as e:
            logger.error(f"Auto-hire failed for {new_agent_name}: {e}")
            self.post_to_channel(
                manager.channel,
                f"Failed to hire {new_agent_name.upper()}: {e}",
                agent_name=hiring_manager,
            )

    def _auto_create_project(self, leader_name: str, initial_response: str):
        """Auto-create and activate a project when CEO starts delegating.

        This enables fully autonomous mode — user just sends a business plan,
        CEO delegates, and the system auto-activates without needing 'start <project>'.
        """
        # Generate a short project key from the response
        project_key = "main"
        projects = self.config.get("projects", {})

        if projects:
            # If projects are defined in crew.yaml, activate the first one
            project_key = next(iter(projects))
            self.project_manager.start_project(project_key)
            logger.info(f"Auto-activated existing project: {project_key}")
        else:
            # Create an ad-hoc project
            self.project_manager.projects["main"] = {
                "name": "Main",
                "description": "Auto-created from initial business plan",
                "context": initial_response[:2000],
            }
            self.project_manager.start_project("main")
            logger.info("Auto-created and activated project: main")

        leader = self.agents.get(leader_name)
        if leader:
            self.post_to_channel(
                leader.channel,
                "Project activated. Autonomous mode ON — I'll keep planning and delegating.",
                agent_name=leader_name,
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
                    todo = self.task_manager.get_tasks(assigned_to=name, status="todo")
                    in_progress = self.task_manager.get_tasks(assigned_to=name, status="in_progress")
                    all_tasks = in_progress + todo
                    if not all_tasks:
                        return f"No open tasks for {name.upper()}."
                    lines = []
                    for t in all_tasks:
                        status = "WORKING" if t["status"] == "in_progress" else t["priority"].upper()
                        lines.append(f"#{t['id']} [{status}] {t['title']}")
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

        if text_lower.startswith("run "):
            # Parse "run feature-dev: description here"
            parts = text[4:].split(":", 1)
            if len(parts) == 2:
                workflow_name = parts[0].strip()
                description = parts[1].strip()
                if workflow_name in self.workflow_engine.load_workflows():
                    threading.Thread(
                        target=self.workflow_engine.run_workflow,
                        args=(workflow_name, description),
                        daemon=True,
                    ).start()
                    return f"Started workflow `{workflow_name}` — I'll post progress updates."
                return f"Unknown workflow: {workflow_name}. Use `workflows` to see available ones."
            return "Usage: run <workflow-name>: description"

        if text_lower == "workflows":
            workflows = self.workflow_engine.load_workflows()
            if not workflows:
                return "No workflows defined. Add a `workflows:` section to crew.yaml."
            lines = []
            for name, steps in workflows.items():
                step_names = " → ".join(s.id for s in steps)
                lines.append(f"  *{name}*: {step_names}")
            return "Available workflows:\n" + "\n".join(lines)

        if text_lower == "workflow status":
            runs = self.workflow_engine.get_active_runs()
            if not runs:
                return "No active workflow runs."
            lines = []
            for run in runs:
                passed = sum(1 for r in run.step_results.values() if r.status == "passed")
                total = len(run.steps)
                lines.append(f"  *{run.workflow_name}* — {passed}/{total} steps complete ({run.status})")
            return "Active workflows:\n" + "\n".join(lines)

        if text_lower == "costs" or text_lower == "cost":
            return f"Cost Tracker\n\n{self.cost_tracker.get_summary()}"

        if text_lower == "team":
            lines = []
            for name, agent in self.agents.items():
                reports = f" → reports to {agent.reports_to.upper()}" if agent.reports_to else ""
                delegates = f" → manages [{', '.join(d.upper() for d in agent.delegates_to)}]" if agent.delegates_to else ""
                lines.append(f"  *{name.upper()}* ({agent.role}) #{agent.channel}{reports}{delegates}")
            return "Current team:\n" + "\n".join(lines)

        if text_lower == "links":
            return self.link_tracker.get_summary()

        if text_lower == "integrations":
            self._show_integrations_manager(channel_name)
            return "Managing integrations..."  # Prevent fall-through to agent call

        if text_lower == "files":
            return self._list_workspace_files()

        if text_lower == "help":
            return (
                "*What's happening?*\n"
                "  `tasks` — What everyone is working on right now\n"
                "  `my tasks` — Tasks for this channel's agent\n"
                "  `team` — Who's on the team and their roles\n"
                "  `files` — All local files the team created\n"
                "  `links` — Notion pages, GitHub repos, designs — all URLs\n"
                "  `status` — Current project status\n"
                "\n*Actions:*\n"
                "  `report` — Ask CEO for a progress report\n"
                "  `hire <description>` — Add a new team member\n"
                "  `cancel #42 reason` — Cancel a task\n"
                "  `stop` — Pause the project\n"
                "  `start <project>` — Resume a project\n"
                "\n*Settings:*\n"
                "  `integrations` — Connect services (GitHub, Notion, etc.)\n"
                "  `costs` — API usage tracker\n"
                "\nOr just type a message — the agent in this channel will respond."
            )

        if text_lower.startswith("add agent") or text_lower.startswith("new agent") or text_lower.startswith("hire"):
            threading.Thread(
                target=self._handle_add_agent,
                args=(text, channel_name),
                daemon=True,
            ).start()
            return "Let me think about what agent would help..."

        return None

    def _handle_agent_reply(self, agent_name: str, text: str, channel_id: str, thread_ts: str, context: str = ""):
        """Run agent call in background thread."""
        try:
            response = self.call_agent(agent_name, text, context)
            channel_name = self.get_channel_name(channel_id)
            if channel_name:
                self.post_to_channel(channel_name, response, thread_ts=thread_ts, agent_name=agent_name)
            else:
                # Fallback to raw post if channel name unknown
                response = markdown_to_slack(response)
                max_len = self.settings.get("slack_max_length", 39000)
                if len(response) > max_len:
                    response = response[:max_len] + "\n\n... (truncated)"
                kwargs = {"channel": channel_id, "text": f"*{agent_name.upper()}*: {response}"}
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts
                client = self.get_agent_client(agent_name)
                client.chat_postMessage(**kwargs)
            self._handle_delegations(agent_name, response)
        except CircuitBrokenError as e:
            logger.error(f"Circuit breaker tripped for {agent_name}: {e}")
            alert = (
                f"ALERT: Agent *{agent_name.upper()}* has been paused — "
                f"circuit breaker tripped after consecutive failures.\n"
                f"Last error: {e.last_error}\n"
                f"The agent will auto-reset after "
                f"{self.settings.get('circuit_reset_minutes', 10)} minutes."
            )
            # Post alert to the agent's own channel
            agent = self.agents.get(agent_name)
            if agent:
                self.post_to_channel(agent.channel, alert, agent_name=agent_name)
            # Also alert the leader channel
            leader = get_leader(self.agents)
            if leader and (not agent or leader.channel != agent.channel):
                self.post_to_channel(leader.channel, alert, agent_name=leader.name)
        except CrewmaticError as e:
            logger.error(f"Agent {agent_name} LLM error: {e}")
            try:
                client = self.get_agent_client(agent_name)
                client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f"Error: {e}",
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Agent reply error ({agent_name}): {e}", exc_info=True)
            try:
                client = self.get_agent_client(agent_name)
                client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text=f"Something went wrong: {type(e).__name__}: {str(e)[:200]}",
                )
            except Exception:
                pass

    def _route_message(self, event, is_mention: bool = False):
        """Shared message routing logic for mentions and direct messages."""
        # Deduplicate events — Slack can deliver the same event twice
        event_ts = event.get("ts")
        if event_ts:
            if event_ts in self._seen_event_ts:
                return
            self._seen_event_ts[event_ts] = True
            # Bound the dedup cache to the last ~100 events
            if len(self._seen_event_ts) > 100:
                excess = len(self._seen_event_ts) - 100
                for key in list(self._seen_event_ts)[:excess]:
                    del self._seen_event_ts[key]

        if not is_mention:
            if event.get("subtype"):
                return
            if event.get("user") in self.all_bot_user_ids:
                return
            if event.get("bot_id"):
                return

        text = event.get("text", "")
        if is_mention:
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts", event.get("ts"))
        channel_name = self.get_channel_name(channel_id)

        agent_name, agent = self.resolve_agent(channel_name, text)
        if not agent:
            if is_mention:
                leader = get_leader(self.agents)
                agent_name = leader.name if leader else list(self.agents.keys())[0]
            else:
                return

        cmd_response = self.handle_command(text, channel_name)
        if cmd_response:
            logger.debug(f"Command '{text}' handled → posting {len(cmd_response)} chars to #{channel_name}")
            try:
                if channel_name:
                    self.post_to_channel(channel_name, cmd_response, thread_ts=thread_ts, agent_name=agent_name)
                else:
                    client = self.get_agent_client(agent_name)
                    client.chat_postMessage(channel=channel_id, text=cmd_response, thread_ts=thread_ts)
            except Exception as e:
                logger.error(f"Failed to post command response for '{text}': {e}")
            return

        logger.info(f"{'Mention' if is_mention else 'Message'} -> {agent_name} in #{channel_name}: {text[:80]}")

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

    def _register_handlers(self):
        """Register Slack event handlers."""

        @self.app.event("app_mention")
        def handle_mention(event, say):
            self._route_message(event, is_mention=True)

        @self.app.event("message")
        def handle_message(event, say):
            self._route_message(event, is_mention=False)

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

        @self.app.action(re.compile(r"^manage_integrations_\d+$"))
        def handle_manage_integrations_checkbox(ack, body):
            ack()

        @self.app.action("manage_integrations_save")
        def handle_manage_integrations_save(ack, body):
            ack()
            selected = []
            block_state = body.get("state", {}).get("values", {})
            for block_id, block_data in block_state.items():
                if not block_id.startswith("manage_integrations_block_"):
                    continue
                for action_data in block_data.values():
                    for opt in action_data.get("selected_options", []):
                        selected.append(opt["value"])
            channel_id = body["channel"]["id"]
            message_ts = body.get("message", {}).get("ts", "")
            self._save_integrations(sorted(selected), channel_id)
            # Replace checkboxes with static summary
            summary = ', '.join(sorted(selected)) if selected else 'none'
            try:
                self.app.client.chat_update(
                    channel=channel_id, ts=message_ts,
                    text=f"Integrations: {summary}",
                    blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"*Integrations:* {summary}"}}],
                )
            except Exception:
                pass

        @self.app.action("manage_integrations_cancel")
        def handle_manage_integrations_cancel(ack, body):
            ack()
            channel_id = body["channel"]["id"]
            message_ts = body.get("message", {}).get("ts", "")
            try:
                self.app.client.chat_update(
                    channel=channel_id, ts=message_ts,
                    text="Integration changes cancelled.",
                    blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Integration changes cancelled."}}],
                )
            except Exception:
                pass

    def _list_workspace_files(self) -> str:
        """List all files and artifacts in the workspace. Instant, no LLM call."""
        config_dir = self.config.get("_config_dir", os.getcwd())
        sections = []

        # Config
        crew_yaml = os.path.join(config_dir, "crew.yaml")
        if os.path.exists(crew_yaml):
            sections.append(f"*Config:*\n  crew.yaml")

        # Data directory
        data_dir = self.config["data_dir"]
        if os.path.isdir(data_dir):
            data_files = sorted(f for f in os.listdir(data_dir) if os.path.isfile(os.path.join(data_dir, f)))
            if data_files:
                lines = [f"  {f} ({os.path.getsize(os.path.join(data_dir, f)) // 1024}KB)" for f in data_files]
                sections.append(f"*Data ({data_dir}):*\n" + "\n".join(lines))

        # Memory directory
        memory_dir = self.config["memory_dir"]
        if os.path.isdir(memory_dir):
            mem_files = sorted(f for f in os.listdir(memory_dir) if os.path.isfile(os.path.join(memory_dir, f)))
            if mem_files:
                lines = [f"  {f} ({os.path.getsize(os.path.join(memory_dir, f)) // 1024}KB)" for f in mem_files]
                sections.append(f"*Agent memories ({memory_dir}):*\n" + "\n".join(lines))

        # Context directory
        context_dir = self.config["context_dir"]
        if os.path.isdir(context_dir):
            ctx_files = sorted(f for f in os.listdir(context_dir) if os.path.isfile(os.path.join(context_dir, f)))
            if ctx_files:
                lines = [f"  {f} ({os.path.getsize(os.path.join(context_dir, f)) // 1024}KB)" for f in ctx_files]
                sections.append(f"*Context files ({context_dir}):*\n" + "\n".join(lines))

        # Project codebase — scan for files created by agents (not hidden, not node_modules)
        codebase = self.project_manager.get_project_codebase()
        if codebase and os.path.isdir(codebase):
            agent_files = []
            skip_dirs = {".git", "node_modules", ".venv", "__pycache__", "data", "memory", "context"}
            for root, dirs, files in os.walk(codebase):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                for f in files:
                    if f in ("crew.yaml", ".env"):
                        continue
                    fpath = os.path.join(root, f)
                    relpath = os.path.relpath(fpath, codebase)
                    agent_files.append(relpath)
                if len(agent_files) > 50:
                    agent_files.append("... (truncated)")
                    break
            if agent_files:
                sections.append(f"*Project files ({codebase}):*\n" + "\n".join(f"  {f}" for f in agent_files))

        if not sections:
            return "No files found in workspace."

        return "Workspace files:\n\n" + "\n\n".join(sections)

    def _show_integrations_manager(self, channel_name: str):
        """Show Block Kit checkboxes to manage integrations."""
        from .integrations import list_integrations

        all_integrations = list_integrations()
        current = self.config.get("integrations", [])

        CHUNK_SIZE = 10
        options_all = []
        initial_all = []
        for integration in sorted(all_integrations, key=lambda i: i["key"]):
            key = integration["key"]
            option = {
                "text": {"type": "plain_text", "text": key},
                "description": {"type": "plain_text", "text": integration.get("description", "")[:75]},
                "value": key,
            }
            options_all.append(option)
            if key in current:
                initial_all.append(option)

        chunks = [options_all[i:i + CHUNK_SIZE] for i in range(0, len(options_all), CHUNK_SIZE)]

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "Manage integrations"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"Currently enabled: {', '.join(current) or 'none'}\nSelect the integrations you want, then click *Save*."}},
        ]

        for idx, chunk in enumerate(chunks):
            chunk_initial = [o for o in chunk if o in initial_all]
            blocks.append({
                "type": "actions",
                "block_id": f"manage_integrations_block_{idx}",
                "elements": [{
                    "type": "checkboxes",
                    "action_id": f"manage_integrations_{idx}",
                    "options": chunk,
                    **({"initial_options": chunk_initial} if chunk_initial else {}),
                }],
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Save"}, "style": "primary", "action_id": "manage_integrations_save"},
                {"type": "button", "text": {"type": "plain_text", "text": "Cancel"}, "action_id": "manage_integrations_cancel"},
            ],
        })

        channel_id = self.channel_name_to_id.get(channel_name)
        if channel_id:
            self.app.client.chat_postMessage(channel=channel_id, text="Manage integrations", blocks=blocks)

    def _save_integrations(self, selected: list[str], channel_id: str):
        """Save updated integrations to crew.yaml and reload config."""
        import yaml
        config_path = self.config.get("_config_path", "")
        if not config_path:
            self.app.client.chat_postMessage(channel=channel_id, text="Cannot find crew.yaml path.")
            return

        try:
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            config["integrations"] = selected
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            self.config["integrations"] = selected
            self.app.client.chat_postMessage(
                channel=channel_id,
                text=f"Integrations updated: {', '.join(selected) or 'none'}",
            )
        except Exception as exc:
            logger.error(f"Failed to save integrations: {exc}")
            self.app.client.chat_postMessage(channel=channel_id, text=f"Failed to save: {exc}")

    def _handle_add_agent(self, request_text: str, channel_name: str):
        """Generate and add a new agent based on natural language request."""
        try:
            import yaml
            from .onboarding.prompts import ADD_AGENT_PROMPT, _get_role_hints
            from .onboarding.crew_generator import merge_agent_into_config

            # Get current agents as YAML for context
            existing_agents = yaml.dump({"agents": {
                name: {"role": a.role, "channel": a.channel, "system_prompt": a.system_prompt[:200]}
                for name, a in self.agents.items()
            }})

            # Extract likely agent name from request for role hints
            role_hints = ""
            import re as _re
            name_match = _re.search(r"called\s+'(\w+)'", request_text)
            if name_match:
                role_hints = _get_role_hints(name_match.group(1))
            elif request_text:
                # Try first word of request as fallback
                role_hints = _get_role_hints(request_text.split()[0] if request_text.split() else "")

            prompt = ADD_AGENT_PROMPT.format(
                request=request_text,
                existing_agents_yaml=existing_agents,
                role_hints=role_hints,
            )

            response = self.claude.call(
                system_prompt="You generate agent configurations for Crewmatic. Output ONLY valid YAML.",
                user_message=prompt,
                model="sonnet",
            )

            # Parse the response as YAML
            agent_data = yaml.safe_load(response)
            if not agent_data or not isinstance(agent_data, dict):
                self.post_to_channel(channel_name, "Failed to generate agent config. Try again with more details.")
                return

            # Get the agent name and config
            agent_name = list(agent_data.keys())[0]
            agent_config = agent_data[agent_name]

            # Save to crew.yaml
            config_path = self.config.get("_config_path", "crew.yaml")
            merge_agent_into_config(config_path, agent_name, agent_config)

            # Hot-reload
            self.reload_agent(agent_name, agent_config)

            self.post_to_channel(
                channel_name,
                f"Added new agent: *{agent_name.upper()}* (#{agent_config['channel']})\n"
                f"Role: {agent_config.get('role', 'worker')}\n"
                f"Worker loop started. The agent is ready to receive tasks.",
            )
        except Exception as e:
            logger.error(f"Failed to add agent: {e}")
            self.post_to_channel(channel_name, f"Failed to add agent: {e}")

    def reload_agent(self, name: str, raw_agent: dict):
        """Hot-reload a single agent. Creates worker loop if new."""
        from .agent_loader import AgentConfig, _default_context_for_role

        role = raw_agent.get("role", "worker")
        agent = AgentConfig(
            name=name,
            channel=raw_agent["channel"],
            system_prompt=raw_agent["system_prompt"],
            model=raw_agent.get("model", "sonnet"),
            role=role,
            tools=raw_agent.get("tools"),
            delegates_to=raw_agent.get("delegates_to", []),
            reports_to=raw_agent.get("reports_to"),
            receives_context=raw_agent.get("receives_context", _default_context_for_role(role)),
            integrations=raw_agent.get("integrations"),
        )
        is_new = name not in self.agents
        self.agents[name] = agent
        # Update scheduler's agent reference too
        self.scheduler.agents = self.agents
        # Keep guardrails in sync
        self.guardrails.set_known_agents(set(self.agents.keys()))
        if is_new and role == "worker":
            threading.Thread(
                target=self.scheduler.agent_work_loop,
                args=(name,),
                daemon=True,
                name=f"worker-{name}",
            ).start()
            logger.info(f"Started worker loop for new agent: {name}")
        self.build_channel_map()

    def queue_business_plan(self, business_description: str):
        """Queue a business plan to be posted to the CEO channel on startup."""
        self._pending_business_plan = business_description

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

        # Start worker loops for all non-leader agents.
        # Managers also execute tasks directly (especially in small teams
        # before they hire workers). Leaders only plan and review.
        for agent_name, agent in self.agents.items():
            if agent.role != "leader":
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

        # Graceful shutdown handler (only works from main thread)
        import signal

        def _shutdown(signum, frame):
            logger.info("Shutdown signal received. Saving state...")
            active = self.project_manager.get_active_project()
            if active:
                try:
                    self._auto_save_leader_context(active)
                except Exception as e:
                    logger.error(f"Failed to save context on shutdown: {e}")
            logger.info("Crewmatic stopped.")
            raise SystemExit(0)

        try:
            signal.signal(signal.SIGINT, _shutdown)
            signal.signal(signal.SIGTERM, _shutdown)
        except ValueError:
            # Not in main thread (e.g. called from wizard callback) — skip signal handlers
            logger.info("Not in main thread — skipping signal handlers")

        # Forward business plan from wizard to CEO — directly invoke agent
        business_plan = getattr(self, "_pending_business_plan", "")
        if business_plan:
            leader = get_leader(self.agents)
            if leader:
                def _forward_plan():
                    time.sleep(5)  # Let Slack connection establish
                    self.post_to_channel(
                        leader.channel,
                        f"Business plan received from setup. Analyzing and starting work...",
                        agent_name=leader.name,
                    )
                    try:
                        response = self.call_agent(
                            leader.name,
                            f"A new business plan has been submitted. Analyze it, break it down "
                            f"into strategic areas, and delegate initial tasks to your team.\n\n"
                            f"BUSINESS PLAN:\n{business_plan}",
                        )
                        self.post_to_channel(leader.channel, response, agent_name=leader.name)
                        self._handle_delegations(leader.name, response)
                        logger.info("CEO processed business plan and created initial tasks")
                    except Exception as e:
                        logger.error(f"CEO failed to process business plan: {e}")
                        self.post_to_channel(
                            leader.channel,
                            f"Failed to process business plan: {e}",
                            agent_name=leader.name,
                        )
                threading.Thread(target=_forward_plan, daemon=True, name="forward-plan").start()
            self._pending_business_plan = ""

        # Start Slack Socket Mode
        handler = SocketModeHandler(self.app, self.app_token)
        handler.start()
