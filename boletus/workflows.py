"""Workflow pipeline engine — multi-step task execution with verification gates."""

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WorkflowStep:
    id: str
    agent: str
    prompt: str
    expects: str = ""
    depends_on: list[str] = field(default_factory=list)
    max_retries: int = 1
    verified_by: str | None = None  # if set, a different agent verifies


@dataclass
class StepResult:
    output: str = ""
    status: str = "pending"  # pending, running, passed, failed, skipped
    retries: int = 0
    verified: bool = False
    verification_output: str = ""


@dataclass
class WorkflowRun:
    workflow_name: str
    trigger_text: str
    steps: list[WorkflowStep]
    step_results: dict[str, StepResult] = field(default_factory=dict)
    status: str = "running"  # running, completed, failed
    created_at: float = field(default_factory=time.time)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


# ---------------------------------------------------------------------------
# Verification prompt
# ---------------------------------------------------------------------------

VERIFICATION_PROMPT = (
    "You are a strict verification judge. A workflow step just produced output.\n\n"
    "STEP EXPECTATION:\n{expects}\n\n"
    "STEP OUTPUT:\n{output}\n\n"
    "Does the output satisfy the expectation above?\n"
    "Answer with exactly YES or NO on the first line, followed by a brief reason."
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class WorkflowEngine:
    """Orchestrates multi-step workflows with dependency resolution and verification."""

    def __init__(
        self,
        config: dict,
        call_agent_fn: Callable[[str, str], str],
        post_fn: Callable[..., None],
        task_manager,
    ):
        self.config = config
        self.call_agent = call_agent_fn
        self.post = post_fn
        self.task_manager = task_manager

        self.data_dir = os.path.join(config.get("data_dir", "data"), "workflows")
        os.makedirs(self.data_dir, exist_ok=True)

        self._active_runs: list[WorkflowRun] = []
        self._lock = threading.Lock()

        self.workflow_defs = self.load_workflows()

        # Resume interrupted runs from previous session
        self._resume_interrupted_runs()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_workflows(self) -> dict[str, list[WorkflowStep]]:
        """Parse workflow definitions from config."""
        raw = self.config.get("workflows", {})
        if not raw or not isinstance(raw, dict):
            return {}

        workflows: dict[str, list[WorkflowStep]] = {}
        for wf_name, wf_def in raw.items():
            steps_raw = wf_def if isinstance(wf_def, list) else wf_def.get("steps", [])
            steps: list[WorkflowStep] = []
            for s in steps_raw:
                if not isinstance(s, dict):
                    logger.warning(f"Workflow '{wf_name}': skipping non-dict step: {s}")
                    continue
                deps = s.get("depends_on", [])
                if isinstance(deps, str):
                    deps = [deps]
                steps.append(WorkflowStep(
                    id=s["id"],
                    agent=s["agent"],
                    prompt=s.get("prompt", ""),
                    expects=s.get("expects", ""),
                    depends_on=deps,
                    max_retries=int(s.get("max_retries", 1)),
                    verified_by=s.get("verified_by"),
                ))
            if steps:
                workflows[wf_name] = steps
                logger.info(f"Loaded workflow '{wf_name}' with {len(steps)} steps")

        return workflows

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def start_workflow(self, workflow_name: str, trigger_text: str) -> WorkflowRun | None:
        """Create a new workflow run. Returns None if workflow not found."""
        steps = self.workflow_defs.get(workflow_name)
        if not steps:
            logger.error(f"Workflow '{workflow_name}' not found")
            return None

        # Deep copy steps so each run is independent
        run_steps = [
            WorkflowStep(
                id=s.id,
                agent=s.agent,
                prompt=s.prompt,
                expects=s.expects,
                depends_on=list(s.depends_on),
                max_retries=s.max_retries,
                verified_by=s.verified_by,
            )
            for s in steps
        ]

        run = WorkflowRun(
            workflow_name=workflow_name,
            trigger_text=trigger_text,
            steps=run_steps,
            step_results={s.id: StepResult() for s in run_steps},
        )

        with self._lock:
            self._active_runs.append(run)

        return run

    def execute_step(self, run: WorkflowRun, step: WorkflowStep) -> StepResult:
        """Execute a single workflow step with the assigned agent."""
        result = run.step_results[step.id]
        result.status = "running"

        # Build prompt: step prompt + trigger text + previous step results
        parts = [f"WORKFLOW: {run.workflow_name}"]
        parts.append(f"ORIGINAL REQUEST: {run.trigger_text}")

        # Include outputs from dependency steps
        for dep_id in step.depends_on:
            dep_result = run.step_results.get(dep_id)
            if dep_result and dep_result.output:
                parts.append(f"OUTPUT FROM STEP '{dep_id}':\n{dep_result.output}")

        parts.append(f"YOUR TASK (step '{step.id}'):\n{step.prompt}")

        if step.expects:
            parts.append(f"ACCEPTANCE CRITERIA: {step.expects}")

        full_prompt = "\n\n---\n\n".join(parts)

        try:
            output = self.call_agent(step.agent, full_prompt)
            result.output = output
        except Exception as e:
            logger.error(f"Step '{step.id}' agent call failed: {e}")
            result.output = f"ERROR: {e}"
            result.status = "failed"
            return result

        # Run verification if step has expectations
        if step.expects:
            passed, verification_output = self.verify_step(run, step, output)
            result.verified = passed
            result.verification_output = verification_output
            result.status = "passed" if passed else "failed"
        else:
            # No expectations — auto-pass
            result.verified = True
            result.status = "passed"

        return result

    def verify_step(
        self, run: WorkflowRun, step: WorkflowStep, output: str,
    ) -> tuple[bool, str]:
        """Verify step output meets expectations.

        If verified_by is set, a different agent checks.
        Otherwise, use a quick LLM check with the step's own agent.
        """
        expects = step.expects

        # Fast-path: check for test/exit-code keywords in output
        expects_lower = expects.lower()
        if "exit code 0" in expects_lower or "tests pass" in expects_lower:
            output_lower = output.lower()
            if "exit code 0" in output_lower or "all tests pass" in output_lower:
                return True, "Test output indicates success."
            if "exit code" in output_lower and "exit code 0" not in output_lower:
                return False, "Test output indicates non-zero exit code."

        # LLM-based verification
        verify_prompt = VERIFICATION_PROMPT.format(expects=expects, output=output[:8000])
        verifier = step.verified_by or step.agent

        try:
            response = self.call_agent(verifier, verify_prompt)
        except Exception as e:
            logger.error(f"Verification call failed for step '{step.id}': {e}")
            return False, f"Verification error: {e}"

        # Parse YES/NO from first line
        first_line = response.strip().split("\n")[0].strip().upper()
        passed = first_line.startswith("YES")
        return passed, response

    def run_workflow(self, workflow_name: str, trigger_text: str) -> WorkflowRun | None:
        """Execute all steps in sequence, respecting dependencies and retries.

        This is a blocking call — run it in a background thread.
        """
        run = self.start_workflow(workflow_name, trigger_text)
        if not run:
            return None

        self._post_progress(run, f"Starting workflow *{workflow_name}* ({len(run.steps)} steps)")

        # Topological order: process steps respecting depends_on
        executed: set[str] = set()
        step_map = {s.id: s for s in run.steps}

        while len(executed) < len(run.steps):
            progress_made = False

            for step in run.steps:
                if step.id in executed:
                    continue

                # Check all dependencies are met
                deps_met = all(
                    dep_id in executed and run.step_results[dep_id].status == "passed"
                    for dep_id in step.depends_on
                )
                if not deps_met:
                    # Check if any dependency failed (no point waiting)
                    deps_failed = any(
                        dep_id in executed and run.step_results[dep_id].status == "failed"
                        for dep_id in step.depends_on
                    )
                    if deps_failed:
                        run.step_results[step.id].status = "skipped"
                        executed.add(step.id)
                        self._post_progress(
                            run,
                            f"Step *{step.id}* skipped — dependency failed",
                        )
                        progress_made = True
                    continue

                # Execute step with retries
                self._post_progress(
                    run,
                    f"Step *{step.id}* started (agent: {step.agent})",
                )

                attempt = 0
                while attempt < step.max_retries:
                    attempt += 1
                    result = self.execute_step(run, step)
                    result.retries = attempt
                    self.save_run(run)  # checkpoint after each step

                    if result.status == "passed":
                        self._post_progress(
                            run,
                            f"Step *{step.id}* passed (attempt {attempt}/{step.max_retries})",
                        )
                        break

                    if attempt < step.max_retries:
                        # Retry with error context
                        logger.info(
                            f"Step '{step.id}' failed on attempt {attempt}, retrying..."
                        )
                        # Augment the step prompt with failure context for retry
                        original_prompt = step.prompt
                        step.prompt = (
                            f"{original_prompt}\n\n"
                            f"PREVIOUS ATTEMPT FAILED.\n"
                            f"Verification feedback: {result.verification_output}\n"
                            f"Previous output: {result.output[:2000]}\n"
                            f"Please fix the issues and try again."
                        )
                    else:
                        # Final failure
                        self._post_progress(
                            run,
                            f"Step *{step.id}* FAILED after {attempt} attempt(s)\n"
                            f"Verification: {result.verification_output[:500]}",
                        )

                # Restore original prompt (in case it was modified for retry)
                executed.add(step.id)
                progress_made = True

            if not progress_made:
                # Deadlock — remaining steps have unresolvable dependencies
                for step in run.steps:
                    if step.id not in executed:
                        run.step_results[step.id].status = "skipped"
                        executed.add(step.id)
                logger.error(f"Workflow '{workflow_name}' has unresolvable dependencies")
                break

        # Determine final status
        statuses = {r.status for r in run.step_results.values()}
        if "failed" in statuses:
            run.status = "failed"
        elif statuses <= {"passed", "skipped"} and "passed" in statuses:
            run.status = "completed"
        else:
            run.status = "failed"

        # Clean up active runs
        with self._lock:
            self._active_runs = [r for r in self._active_runs if r.run_id != run.run_id]

        self._post_progress(run, f"Workflow *{workflow_name}* finished: *{run.status.upper()}*")
        self.save_run(run)

        return run

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_run(self, run: WorkflowRun):
        """Persist workflow run state to JSON."""
        data = {
            "run_id": run.run_id,
            "workflow_name": run.workflow_name,
            "trigger_text": run.trigger_text,
            "status": run.status,
            "created_at": run.created_at,
            "steps": [
                {
                    "id": s.id,
                    "agent": s.agent,
                    "prompt": s.prompt,
                    "expects": s.expects,
                    "depends_on": s.depends_on,
                    "max_retries": s.max_retries,
                    "verified_by": s.verified_by,
                }
                for s in run.steps
            ],
            "step_results": {
                step_id: asdict(result)
                for step_id, result in run.step_results.items()
            },
        }

        filename = f"{run.workflow_name}_{run.run_id}.json"
        filepath = os.path.join(self.data_dir, filename)
        tmp = filepath + ".tmp"

        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, filepath)

        logger.info(f"Saved workflow run: {filepath}")

    def load_run(self, filepath: str) -> WorkflowRun | None:
        """Load a workflow run from a JSON file."""
        try:
            with open(filepath) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load workflow run from {filepath}: {e}")
            return None

        steps = [
            WorkflowStep(
                id=s["id"],
                agent=s["agent"],
                prompt=s.get("prompt", ""),
                expects=s.get("expects", ""),
                depends_on=s.get("depends_on", []),
                max_retries=s.get("max_retries", 1),
                verified_by=s.get("verified_by"),
            )
            for s in data.get("steps", [])
        ]

        step_results = {}
        for step_id, sr in data.get("step_results", {}).items():
            step_results[step_id] = StepResult(
                output=sr.get("output", ""),
                status=sr.get("status", "pending"),
                retries=sr.get("retries", 0),
                verified=sr.get("verified", False),
                verification_output=sr.get("verification_output", ""),
            )

        run = WorkflowRun(
            workflow_name=data["workflow_name"],
            trigger_text=data.get("trigger_text", ""),
            steps=steps,
            step_results=step_results,
            status=data.get("status", "completed"),
            created_at=data.get("created_at", 0.0),
            run_id=data.get("run_id", "unknown"),
        )
        return run

    def get_active_runs(self) -> list[WorkflowRun]:
        """List active workflow runs."""
        with self._lock:
            return list(self._active_runs)

    def list_completed_runs(self) -> list[str]:
        """List completed run filenames from the data directory."""
        try:
            return sorted(
                f for f in os.listdir(self.data_dir)
                if f.endswith(".json")
            )
        except OSError:
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resume_interrupted_runs(self):
        """On startup, find workflow runs that were interrupted and resume them."""
        for fname in self.list_completed_runs():
            fpath = os.path.join(self.data_dir, fname)
            run = self.load_run(fpath)
            if run and run.status == "running":
                logger.info(f"Found interrupted workflow run: {run.workflow_name} ({run.run_id})")
                # Resume in background thread
                threading.Thread(
                    target=self._resume_run,
                    args=(run, fpath),
                    daemon=True,
                    name=f"wf-resume-{run.run_id}",
                ).start()

    def _resume_run(self, run: WorkflowRun, filepath: str):
        """Resume an interrupted workflow run from where it left off."""
        with self._lock:
            self._active_runs.append(run)

        self._post_progress(run, f"Resuming workflow *{run.workflow_name}* after restart")

        # Figure out which steps already completed
        executed: set[str] = set()
        for step in run.steps:
            sr = run.step_results.get(step.id)
            if sr and sr.status in ("passed", "failed", "skipped"):
                executed.add(step.id)

        # Continue with remaining steps (same logic as run_workflow)
        step_map = {s.id: s for s in run.steps}

        while len(executed) < len(run.steps):
            progress_made = False

            for step in run.steps:
                if step.id in executed:
                    continue

                deps_met = all(
                    dep_id in executed and run.step_results[dep_id].status == "passed"
                    for dep_id in step.depends_on
                )
                if not deps_met:
                    deps_failed = any(
                        dep_id in executed and run.step_results[dep_id].status == "failed"
                        for dep_id in step.depends_on
                    )
                    if deps_failed:
                        run.step_results[step.id].status = "skipped"
                        executed.add(step.id)
                        progress_made = True
                    continue

                self._post_progress(run, f"Step *{step.id}* started (agent: {step.agent})")
                result = self.execute_step(run, step)
                result.retries = 1
                executed.add(step.id)
                progress_made = True
                self.save_run(run)  # checkpoint after each step

                if result.status == "passed":
                    self._post_progress(run, f"Step *{step.id}* passed")
                else:
                    self._post_progress(run, f"Step *{step.id}* FAILED")

            if not progress_made:
                for step in run.steps:
                    if step.id not in executed:
                        run.step_results[step.id].status = "skipped"
                        executed.add(step.id)
                break

        # Final status
        statuses = {r.status for r in run.step_results.values()}
        if "failed" in statuses:
            run.status = "failed"
        elif statuses <= {"passed", "skipped"} and "passed" in statuses:
            run.status = "completed"
        else:
            run.status = "failed"

        with self._lock:
            self._active_runs = [r for r in self._active_runs if r.run_id != run.run_id]

        self._post_progress(run, f"Workflow *{run.workflow_name}* finished: *{run.status.upper()}*")
        self.save_run(run)

    def _post_progress(self, run: WorkflowRun, message: str):
        """Post workflow progress to the leader's channel."""
        agents = self.config.get("agents", {})
        # Find the leader channel to post updates
        leader_channel = None
        for name, agent_def in agents.items():
            if agent_def.get("role") == "leader":
                leader_channel = agent_def.get("channel")
                break

        if not leader_channel:
            # Fall back to the first agent's channel
            for name, agent_def in agents.items():
                leader_channel = agent_def.get("channel")
                break

        if leader_channel:
            prefix = f"[Workflow: {run.workflow_name} | {run.run_id}]"
            try:
                self.post(leader_channel, f"{prefix} {message}")
            except Exception as e:
                logger.error(f"Failed to post workflow progress: {e}")
        else:
            logger.info(f"Workflow progress (no channel): {message}")
