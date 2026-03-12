"""Scheduled loops — planning, worker execution, and reporting."""

import logging
import time
from datetime import datetime

from .agent_loader import AgentConfig, get_leader, get_delegation_targets

logger = logging.getLogger(__name__)

# Default prompt templates — users can override via crew.yaml prompts: section
DEFAULT_PLANNING_TEMPLATE = """You are the {leader_name} planning work. Current time: {timestamp}.
Active project: {project_name}
{project_context}

Review your memory, the task board, and team channel updates.
You run this team autonomously. The owner ({owner_mention}) is your investor — not your manager.

Your team:
{team_list}

WHAT TO DO NOW:
1. Check what your team has COMPLETED since last planning.
2. Review what's still OPEN on the task board. Don't duplicate.
3. Decide the next concrete steps to push forward.
4. Create NEW tasks for your team.

To delegate tasks, use this EXACT format (one per line):
{delegation_format}

Be SPECIFIC. Don't repeat tasks already on the board."""

DEFAULT_STANDUP_TEMPLATE = """Brief standup update (3-5 bullet points max):
1. What did you complete since last update?
2. What are you working on now?
3. Any blockers?
Keep it SHORT."""

DEFAULT_REPORT_TEMPLATE = """You are the {leader_name} writing a progress report for the owner ({owner_mention}).
Current time: {timestamp}.

This is a SUMMARY REPORT. The owner wants to know:
1. What concrete progress was made
2. Key metrics (if any)
3. What's planned next
4. Any decisions that need owner approval

Check your memory file and task board for details. Keep it concise — bullet points."""

DEFAULT_TASK_EXECUTION_TEMPLATE = """Execute this task:

{task_title}

Details: {task_details}
Assigned by: {created_by}

Do the actual work. If it involves code, write/edit the code.
If it involves research, do the research and write findings.
Report exactly what you did and what the result is."""


class Scheduler:
    """Manages all scheduled loops: planning, worker execution, and reports."""

    def __init__(
        self,
        agents: dict[str, AgentConfig],
        config: dict,
        task_manager,
        project_manager,
        call_agent_fn,
        post_fn,
        handle_delegations_fn,
        guardrails=None,
    ):
        self.agents = agents
        self.config = config
        self.settings = config.get("settings", {})
        self.prompts = config.get("prompts", {})
        self.task_manager = task_manager
        self.project_manager = project_manager
        self.call_agent = call_agent_fn
        self.post = post_fn
        self.handle_delegations = handle_delegations_fn
        self.guardrails = guardrails

        owner = config.get("owner", {})
        self.owner_mention = owner.get("slack_id", "")
        if self.owner_mention and not self.owner_mention.startswith("<@"):
            self.owner_mention = f"<@{self.owner_mention}>"

    def _get_template(self, name: str, default: str) -> str:
        return self.prompts.get(name, default)

    def _build_team_list(self, leader: AgentConfig) -> str:
        lines = []
        for target_name in leader.delegates_to:
            target = self.agents.get(target_name)
            if target:
                # Extract first line of system prompt as role summary
                first_line = target.system_prompt.strip().split("\n")[0][:100]
                lines.append(f"- @{target_name}: {first_line}")
        return "\n".join(lines) if lines else "- (no team members configured)"

    def _build_delegation_format(self, leader: AgentConfig) -> str:
        lines = []
        for target_name in leader.delegates_to:
            lines.append(f"@{target_name}: specific task description")
        return "\n".join(lines) if lines else "@agent: task description"

    def run_planning(self):
        """Leader plans work and creates tasks for the team."""
        leader = get_leader(self.agents)
        if not leader:
            logger.warning("No leader agent — skipping planning")
            return

        project_key = self.project_manager.get_active_project()
        project_ctx = self.project_manager.get_project_context()

        template = self._get_template("planning", DEFAULT_PLANNING_TEMPLATE)
        prompt = template.format(
            leader_name=leader.name.upper(),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            project_name=project_key or "NONE — IDLE MODE",
            project_context=project_ctx,
            owner_mention=self.owner_mention,
            team_list=self._build_team_list(leader),
            delegation_format=self._build_delegation_format(leader),
        )

        logger.info("Running leader planning...")
        response = self.call_agent(leader.name, prompt)
        self.post(leader.channel, f"Planning — {datetime.now().strftime('%H:%M')}\n\n{response}", agent_name=leader.name)
        self.handle_delegations(leader.name, response)
        return response

    def run_standup(self):
        """Each manager/worker reports briefly, then leader synthesizes."""
        leader = get_leader(self.agents)
        if not leader:
            return

        standup_template = self._get_template("standup", DEFAULT_STANDUP_TEMPLATE)
        standup_agents = [name for name in leader.delegates_to if name in self.agents]
        reports = []

        for agent_name in standup_agents:
            try:
                report = self.call_agent(agent_name, standup_template)
                reports.append(f"{agent_name.upper()}\n{report}")
            except Exception as e:
                reports.append(f"{agent_name.upper()}\nNo response (error: {str(e)[:100]})")

        standup_text = f"Team Standup — {datetime.now().strftime('%d.%m. %H:%M')}\n\n" + "\n\n---\n\n".join(reports)
        self.post(leader.channel, standup_text, agent_name=leader.name)

        # Leader synthesizes
        synthesis_prompt = (
            f"Your team just gave standup updates:\n\n{standup_text}\n\n"
            f"Summarize blockers, decide priorities, and delegate next tasks."
        )
        response = self.call_agent(leader.name, synthesis_prompt)
        self.post(leader.channel, response, agent_name=leader.name)
        self.handle_delegations(leader.name, response)

    def run_report(self):
        """Leader sends a progress report to the owner."""
        leader = get_leader(self.agents)
        if not leader:
            return

        template = self._get_template("report", DEFAULT_REPORT_TEMPLATE)
        prompt = template.format(
            leader_name=leader.name.upper(),
            owner_mention=self.owner_mention,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        logger.info("Running leader report...")
        response = self.call_agent(leader.name, prompt)
        self.post(leader.channel, response, agent_name=leader.name)

    def agent_work_loop(self, agent_name: str):
        """Continuous work loop for a single agent."""
        poll_interval = self.settings.get("worker_poll_interval", 60)
        time.sleep(30)  # Wait for Slack connection
        logger.info(f"[{agent_name.upper()}] Worker loop started")

        while True:
            try:
                if not self.project_manager.is_active():
                    time.sleep(30)
                    continue

                # Check guardrails before claiming work
                if self.guardrails:
                    allowed, reason = self.guardrails.can_execute(agent_name)
                    if not allowed:
                        logger.warning(f"[{agent_name.upper()}] Skipping — {reason}")
                        time.sleep(poll_interval)
                        continue

                task = self.task_manager.claim_task(agent_name)
                if not task:
                    time.sleep(poll_interval)
                    continue

                task_id = task["id"]
                task_title = task["title"]
                agent = self.agents[agent_name]
                logger.info(f"[{agent_name.upper()}] Executing task #{task_id}: {task_title[:80]}")

                self.post(agent.channel, f"Starting task #{task_id}: {task_title}", agent_name=agent_name)

                # Execute the task
                exec_template = self._get_template("task_execution", DEFAULT_TASK_EXECUTION_TEMPLATE)
                exec_prompt = exec_template.format(
                    task_title=task_title,
                    task_details=task.get("details", "None"),
                    created_by=task.get("created_by", "leader").upper(),
                )
                response = self.call_agent(agent_name, exec_prompt)

                # Check for stale claim
                current_task = self.task_manager.get_task_by_id(task_id)
                if current_task and current_task.get("claim_generation", 0) != task.get("claim_generation", 0):
                    logger.warning(f"[{agent_name.upper()}] Task #{task_id} re-claimed. Discarding stale result.")
                    continue

                self.task_manager.complete_task(task_id, result=response[:500])
                self.post(
                    agent.channel,
                    f"Completed task #{task_id}: {task_title}\n\n{response}",
                    agent_name=agent_name,
                )
                logger.info(f"[{agent_name.upper()}] Completed task #{task_id}")
                self.handle_delegations(agent_name, response)
                time.sleep(10)

            except Exception as e:
                logger.error(f"[{agent_name.upper()}] Work loop error: {e}")
                time.sleep(poll_interval)

    def planning_loop(self):
        """Leader continuously plans work when task board runs low."""
        planning_interval = self.settings.get("planning_interval", 1800)
        planning_cooldown = self.settings.get("planning_cooldown", 600)
        planning_threshold = self.settings.get("planning_threshold", 3)
        archive_interval = 86400

        time.sleep(45)

        # Initial planning
        logger.info("Leader initial planning kickoff...")
        try:
            self.run_planning()
        except Exception as e:
            logger.error(f"Initial planning failed: {e}")

        last_archive = 0.0

        while True:
            if not self.project_manager.is_active():
                time.sleep(60)
                continue

            # Periodic archival
            if time.time() - last_archive > archive_interval:
                try:
                    archived = self.task_manager.archive_old_tasks()
                    if archived:
                        logger.info(f"Archived {archived} old tasks")
                    last_archive = time.time()
                except Exception as e:
                    logger.error(f"Archive failed: {e}")

            try:
                open_tasks = self.task_manager.count_open_tasks()
                if open_tasks < planning_threshold:
                    logger.info(f"Task board low ({open_tasks} open). Planning more work...")
                    self.run_planning()
                    time.sleep(planning_cooldown)
                else:
                    logger.info(f"Task board has {open_tasks} open tasks. Waiting...")
                    time.sleep(planning_interval)
            except Exception as e:
                logger.error(f"Planning loop error: {e}")
                time.sleep(planning_cooldown)

    def report_loop(self):
        """Separate loop for scheduled reports."""
        report_hours = self.settings.get("report_hours", [9, 16, 22])
        time.sleep(15)

        ran_today: set[int] = set()
        last_date = datetime.now().date()

        while True:
            now = datetime.now()
            if now.date() != last_date:
                ran_today.clear()
                last_date = now.date()

            current_hour = now.hour
            if current_hour in report_hours and current_hour not in ran_today:
                ran_today.add(current_hour)
                logger.info(f"Scheduled report at {now.strftime('%H:%M')}")
                try:
                    self.run_report()
                except Exception as e:
                    logger.error(f"Scheduled report failed: {e}")

            time.sleep(60)
