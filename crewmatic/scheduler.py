"""Scheduled loops — planning, worker execution, and reporting."""

import logging
import time
from datetime import datetime

from .agent_loader import AgentConfig, get_leader, get_delegation_targets, get_effective_channel
from .context import append_agent_memory
from .delegation import parse_delegations

logger = logging.getLogger(__name__)

# Default prompt templates — users can override via crew.yaml prompts: section
DEFAULT_PLANNING_TEMPLATE = """You are the {leader_name} planning work. Current time: {timestamp}.
Active project: {project_name}
{project_context}

Review your memory, the task board, and team channel updates.
You run this team autonomously. The owner ({owner_mention}) is your investor — not your manager.

Your current team:
{team_list}

WHAT TO DO NOW:
1. Check what your team has COMPLETED since last planning.
2. Review what's still OPEN on the task board.
3. Assess if your team has the right people. If not — HIRE.
4. Decide the next concrete steps to push forward.
5. Create NEW tasks only if needed.

CRITICAL — DO NOT RE-DELEGATE EXISTING TASKS:
If a task is already on the board (status: todo or in_progress), do NOT
delegate it again. Your team will pick it up automatically. Re-delegating
creates duplicates and wastes everyone's time. Only delegate NEW work
that is not already covered by an existing task. If the board already has
the right tasks, just say "Board looks good, no new tasks needed" and
move on.

DELEGATING TASKS (existing team):
{delegation_format}

You can set priority by adding [HIGH] or [LOW] before the description:
@agent [HIGH]: Fix critical auth bug immediately
@agent [LOW]: Update documentation when free
@agent: Normal priority task (default: medium)

HIRING NEW TEAM MEMBERS:
If you need a role that doesn't exist yet, just delegate to it:
@sales_rep: Build a list of 50 target companies and start cold outreach
@data_analyst: Analyze our conversion funnel and identify drop-off points
@content_writer: Write 5 blog posts about our product for SEO

The system will automatically create the agent, assign them to your team,
and give them the task. Only hire when the workload justifies it.

SELF-ASSESSMENT:
If something isn't working (tasks failing, wrong strategy, blocked), ESCALATE:
- Report the problem clearly to the owner
- Adjust your strategy — don't keep doing what isn't working

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
3. Team composition — who's on the team and is it optimal?
4. Cost update: {cost_summary}
5. What's planned next
6. Any decisions that need owner approval

Check your memory file and task board for details. Keep it concise — bullet points."""

DEFAULT_TASK_EXECUTION_TEMPLATE = """Execute this task:

{task_title}

Details: {task_details}
Assigned by: {created_by}

Do the actual work. If it involves code, write/edit the code.
If it involves research, do the research and write findings.
Report exactly what you did and what the result is.

IMPORTANT — If you hit a blocker or realize the approach is wrong:
1. Describe what you tried and why it failed
2. Suggest an alternative approach
3. Start your response with ESCALATION: so your manager can act on it"""


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
        cost_summary_fn=None,
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
        self.cost_summary_fn = cost_summary_fn

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

        # Persist planning decisions to leader memory
        memory_dir = self.config.get("memory_dir", "./memory")
        append_agent_memory(
            leader.name, memory_dir,
            f"Planning session — delegated tasks:\n{response[:500]}",
        )
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

        # Persist standup synthesis to leader memory
        memory_dir = self.config.get("memory_dir", "./memory")
        append_agent_memory(
            leader.name, memory_dir,
            f"Standup synthesis:\n{response[:500]}",
        )

    def run_report(self):
        """Leader sends a progress report to the owner."""
        leader = get_leader(self.agents)
        if not leader:
            return

        cost_summary = self.cost_summary_fn() if self.cost_summary_fn else "Not tracked"
        template = self._get_template("report", DEFAULT_REPORT_TEMPLATE)
        prompt = template.format(
            leader_name=leader.name.upper(),
            owner_mention=self.owner_mention,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            cost_summary=cost_summary,
        )

        logger.info("Running leader report...")
        response = self.call_agent(leader.name, prompt)
        self.post(leader.channel, response, agent_name=leader.name)

        # Ping owner with the report
        if self.owner_mention:
            self.post(leader.channel, f"{self.owner_mention} Progress report above.", agent_name=leader.name)

        # Persist report to leader memory
        memory_dir = self.config.get("memory_dir", "./memory")
        append_agent_memory(
            leader.name, memory_dir,
            f"Progress report sent to owner:\n{response[:500]}",
        )

    def agent_work_loop(self, agent_name: str):
        """Continuous work loop for a single agent."""
        poll_interval = self.settings.get("worker_poll_interval", 60)
        time.sleep(30)  # Wait for Slack connection
        logger.info(f"[{agent_name.upper()}] Worker loop started")

        while True:
            try:
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

                channel = get_effective_channel(agent_name, self.agents)
                self.post(channel, f"Starting task #{task_id}: {task_title}", agent_name=agent_name)

                # Execute the task
                exec_template = self._get_template("task_execution", DEFAULT_TASK_EXECUTION_TEMPLATE)
                exec_prompt = exec_template.format(
                    task_title=task_title,
                    task_details=task.get("details", "None"),
                    created_by=task.get("created_by", "leader").upper(),
                )
                # Include rejection feedback if this task was previously rejected
                rejection = task.get("rejection_feedback", "")
                if rejection:
                    exec_prompt += (
                        f"\n\nPREVIOUS ATTEMPT WAS REJECTED. Manager feedback:\n{rejection}\n"
                        f"Address this feedback in your new attempt."
                    )
                response = self.call_agent(agent_name, exec_prompt)

                # Check for stale claim
                current_task = self.task_manager.get_task_by_id(task_id)
                if current_task and current_task.get("claim_generation", 0) != task.get("claim_generation", 0):
                    logger.warning(f"[{agent_name.upper()}] Task #{task_id} re-claimed. Discarding stale result.")
                    continue

                # Handle escalations — worker hit a blocker
                if response.strip().upper().startswith("ESCALATION:"):
                    reviewer = agent.reports_to
                    if reviewer and reviewer in self.agents:
                        reviewer_channel = get_effective_channel(reviewer, self.agents)
                        self.post(
                            reviewer_channel,
                            f"ESCALATION from {agent_name.upper()} on task #{task_id}:\n\n{response}",
                            agent_name=agent_name,
                        )
                        logger.info(f"[{agent_name.upper()}] Escalated task #{task_id} to {reviewer}")
                        self.task_manager.reset_task(task_id)
                        continue

                # Verify result if agent reports to a manager
                verified = True
                reviewer = agent.reports_to
                reviewer_agent = self.agents.get(reviewer) if reviewer else None

                if reviewer_agent and reviewer_agent.role in ("leader", "manager"):
                    verified = self._verify_task_result(
                        reviewer, reviewer_agent, agent_name, task_id, task_title, response
                    )

                if verified:
                    completed = self.task_manager.complete_task(task_id, result=response[:500])
                    if not completed:
                        logger.warning(f"[{agent_name.upper()}] Task #{task_id} could not be marked complete (cancelled or missing)")
                        continue
                    channel = get_effective_channel(agent_name, self.agents)
                    self.post(
                        channel,
                        f"Completed task #{task_id}: {task_title}\n\n{response}",
                        agent_name=agent_name,
                    )
                    logger.info(f"[{agent_name.upper()}] Completed task #{task_id}")
                    self.handle_delegations(agent_name, response)

                    # Ping owner for high-priority completions
                    if task.get("priority") == "high" and self.owner_mention:
                        leader = get_leader(self.agents)
                        if leader:
                            self.post(
                                leader.channel,
                                f"{self.owner_mention} High-priority task completed: #{task_id} {task_title}",
                                agent_name=agent_name,
                            )

                    # Auto-persist to memory
                    memory_dir = self.config.get("memory_dir", "./memory")
                    summary = response[:300].strip()
                    append_agent_memory(
                        agent_name, memory_dir,
                        f"Completed task #{task_id}: {task_title}\n\nResult: {summary}",
                    )
                else:
                    # Rejected — cancel original to avoid duplicates.
                    # The reviewer's delegation (from _verify_task_result) creates the fix task.
                    self.task_manager.cancel_task(task_id, reason=f"Rejected by {reviewer}")
                    logger.info(f"[{agent_name.upper()}] Task #{task_id} rejected by {reviewer}, cancelled")
                time.sleep(10)

            except Exception as e:
                logger.error(f"[{agent_name.upper()}] Work loop error: {e}")
                time.sleep(poll_interval)

    def _verify_task_result(
        self,
        reviewer_name: str,
        reviewer_agent: AgentConfig,
        worker_name: str,
        task_id: int,
        task_title: str,
        result: str,
    ) -> bool:
        """Ask a manager to verify a worker's task result.

        Returns True if approved, False if rejected (task should be retried).
        """
        prompt = (
            f"Your team member {worker_name.upper()} completed task #{task_id}:\n\n"
            f"**Task:** {task_title}\n\n"
            f"**Result:**\n{result[:2000]}\n\n"
            f"Review this result. Does it meet the task requirements?\n"
            f"- If YES: respond with APPROVED and a brief note.\n"
            f"- If NO: respond with REJECTED and specific feedback on what needs to change.\n"
            f"  Then delegate the fix back: @{worker_name}: specific fix instructions\n\n"
            f"Start your response with either APPROVED or REJECTED."
        )

        try:
            review = self.call_agent(reviewer_name, prompt)
            review_upper = review.strip()[:100].upper()

            if "REJECTED" in review_upper:
                # Post rejection feedback to the team channel
                channel = get_effective_channel(reviewer_name, self.agents)
                self.post(
                    channel,
                    f"Review of task #{task_id} ({worker_name.upper()}): REJECTED\n\n{review}",
                    agent_name=reviewer_name,
                )
                # Parse any re-delegation from the review
                self.handle_delegations(reviewer_name, review)
                # If reviewer didn't re-delegate, reset the original task so worker retries
                delegations = parse_delegations(review, set(self.agents.keys()))
                if not delegations:
                    self.task_manager.reset_task(task_id, feedback=review[:500])
                return False

            # Approved (default if unclear)
            channel = get_effective_channel(reviewer_name, self.agents)
            self.post(
                channel,
                f"Review of task #{task_id} ({worker_name.upper()}): Approved",
                agent_name=reviewer_name,
            )
            return True

        except Exception as e:
            # If review fails, escalate to leader instead of blindly approving
            logger.warning(f"Review by {reviewer_name} failed: {e}. Escalating to leader.")
            leader = get_leader(self.agents)
            if leader and leader.name != reviewer_name:
                self.post(
                    leader.channel,
                    f"Review by {reviewer_name.upper()} failed for task #{task_id}: {e}\n"
                    f"Task auto-approved — please verify.",
                    agent_name=reviewer_name,
                )
            return True

    def planning_loop(self):
        """Leader continuously plans work when task board runs low."""
        planning_interval = self.settings.get("planning_interval", 1800)
        planning_cooldown = self.settings.get("planning_cooldown", 600)
        planning_threshold = self.settings.get("planning_threshold", 3)
        archive_interval = 86400
        stuck_replan_count = 0

        time.sleep(45)

        # Don't do initial planning if no tasks and no project — wait for user
        # to send a business plan first (CEO will create tasks via delegation)
        if self.project_manager.is_active() or self.task_manager.count_open_tasks() > 0:
            logger.info("Leader initial planning kickoff...")
            try:
                self.run_planning()
            except Exception as e:
                logger.error(f"Initial planning failed: {e}")
        else:
            logger.info("No active project and no tasks. Waiting for user input...")

        last_archive = 0.0

        while True:
            # Plan if: there are tasks to manage OR a project is active
            has_work = (
                self.project_manager.is_active()
                or self.task_manager.count_open_tasks() > 0
            )
            if not has_work:
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
                stuck_tasks = self.task_manager.get_stuck_tasks()

                if stuck_tasks:
                    # Avoid death loop: if we already replanned for stuck tasks
                    # recently and they're STILL stuck, back off exponentially
                    if stuck_replan_count >= 3:
                        backoff = min(planning_interval * 2, 3600)
                        logger.warning(
                            f"{len(stuck_tasks)} stuck tasks still unresolved after "
                            f"{stuck_replan_count} replans. Backing off {backoff}s. "
                            f"Agents may not have worker loops or tasks may be unassignable."
                        )
                        time.sleep(backoff)
                        stuck_replan_count += 1
                        continue
                    logger.info(f"{len(stuck_tasks)} stuck tasks detected. Triggering replanning...")
                    self.run_planning()
                    stuck_replan_count += 1
                    time.sleep(planning_cooldown)
                elif open_tasks < planning_threshold:
                    stuck_replan_count = 0  # Reset — board is healthy
                    logger.info(f"Task board low ({open_tasks} open). Planning more work...")
                    self.run_planning()
                    time.sleep(planning_cooldown)
                else:
                    stuck_replan_count = 0  # Reset — board is healthy
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
