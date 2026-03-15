"""Tests for boletus.workflows — workflow pipeline engine."""

import json
import os
import pytest
from unittest.mock import MagicMock

from boletus.workflows import WorkflowEngine, WorkflowStep, WorkflowRun, StepResult


def make_config(tmp_path, workflows=None):
    """Create a minimal config with optional workflow definitions."""
    config = {
        "data_dir": str(tmp_path),
        "settings": {},
    }
    if workflows:
        config["workflows"] = workflows
    return config


def make_engine(tmp_path, workflows=None, call_fn=None):
    """Create a WorkflowEngine with sensible defaults."""
    config = make_config(tmp_path, workflows)
    return WorkflowEngine(
        config,
        call_fn or MagicMock(return_value="ok"),
        MagicMock(),
        MagicMock(),
    )


class TestWorkflowEngine:
    def test_load_workflows_empty(self, tmp_path):
        """No workflows defined returns empty dict."""
        engine = make_engine(tmp_path)
        assert engine.workflow_defs == {}

    def test_load_workflows_parses_steps(self, tmp_path):
        """Workflow steps are parsed correctly."""
        engine = make_engine(tmp_path, workflows={
            "test-flow": {
                "steps": [
                    {"id": "plan", "agent": "cto", "prompt": "Plan it", "expects": "Plan ready"},
                    {"id": "build", "agent": "dev", "prompt": "Build it", "depends_on": ["plan"]},
                ]
            }
        })
        wfs = engine.workflow_defs
        assert "test-flow" in wfs
        assert len(wfs["test-flow"]) == 2
        assert wfs["test-flow"][0].id == "plan"
        assert wfs["test-flow"][0].expects == "Plan ready"
        assert wfs["test-flow"][1].depends_on == ["plan"]

    def test_load_workflows_list_format(self, tmp_path):
        """Workflow defined as a bare list (not dict with 'steps' key)."""
        engine = make_engine(tmp_path, workflows={
            "simple": [
                {"id": "s1", "agent": "dev", "prompt": "do it"},
            ]
        })
        assert "simple" in engine.workflow_defs
        assert engine.workflow_defs["simple"][0].id == "s1"

    def test_load_workflows_depends_on_string(self, tmp_path):
        """depends_on as a single string is converted to a list."""
        engine = make_engine(tmp_path, workflows={
            "flow": {
                "steps": [
                    {"id": "a", "agent": "dev", "prompt": "x"},
                    {"id": "b", "agent": "dev", "prompt": "y", "depends_on": "a"},
                ]
            }
        })
        assert engine.workflow_defs["flow"][1].depends_on == ["a"]

    def test_start_workflow_unknown(self, tmp_path):
        """Starting unknown workflow returns None."""
        engine = make_engine(tmp_path)
        result = engine.start_workflow("nonexistent", "test")
        assert result is None

    def test_start_workflow_creates_run(self, tmp_path):
        """Starting a known workflow returns a WorkflowRun."""
        engine = make_engine(tmp_path, workflows={
            "build": {
                "steps": [
                    {"id": "s1", "agent": "dev", "prompt": "code"},
                ]
            }
        })
        run = engine.start_workflow("build", "make something")
        assert run is not None
        assert isinstance(run, WorkflowRun)
        assert run.workflow_name == "build"
        assert run.trigger_text == "make something"
        assert run.status == "running"
        assert "s1" in run.step_results
        assert run.step_results["s1"].status == "pending"

    def test_execute_step_no_expects_autopasses(self, tmp_path):
        """Step without expects auto-passes."""
        call_fn = MagicMock(return_value="built it")
        engine = make_engine(tmp_path, workflows={
            "flow": {"steps": [{"id": "s1", "agent": "dev", "prompt": "do it"}]}
        }, call_fn=call_fn)
        run = engine.start_workflow("flow", "test")
        step = run.steps[0]
        result = engine.execute_step(run, step)
        assert result.status == "passed"
        assert result.verified is True
        assert result.output == "built it"

    def test_execute_step_with_expects_calls_verify(self, tmp_path):
        """Step with expects triggers verification."""
        call_fn = MagicMock(return_value="YES looks good")
        engine = make_engine(tmp_path, workflows={
            "flow": {"steps": [{"id": "s1", "agent": "dev", "prompt": "do it", "expects": "code compiles"}]}
        }, call_fn=call_fn)
        run = engine.start_workflow("flow", "test")
        step = run.steps[0]
        result = engine.execute_step(run, step)
        assert result.status == "passed"

    def test_execute_step_agent_error(self, tmp_path):
        """Step fails if agent call raises."""
        call_fn = MagicMock(side_effect=Exception("LLM down"))
        engine = make_engine(tmp_path, workflows={
            "flow": {"steps": [{"id": "s1", "agent": "dev", "prompt": "do it"}]}
        }, call_fn=call_fn)
        run = engine.start_workflow("flow", "test")
        result = engine.execute_step(run, run.steps[0])
        assert result.status == "failed"
        assert "ERROR" in result.output

    def test_verify_step_fast_path_tests_pass(self, tmp_path):
        """Verification fast-path detects 'all tests pass' keyword."""
        call_fn = MagicMock(return_value="YES")
        engine = make_engine(tmp_path, call_fn=call_fn)
        step = WorkflowStep(id="test", agent="tester", prompt="test", expects="All tests pass")
        run = MagicMock()
        passed, msg = engine.verify_step(run, step, "Ran 15 tests\n\nall tests pass in 0.5s")
        assert passed
        assert "success" in msg.lower()

    def test_verify_step_fast_path_exit_code_0(self, tmp_path):
        """Verification fast-path detects exit code 0."""
        engine = make_engine(tmp_path)
        step = WorkflowStep(id="build", agent="dev", prompt="build", expects="exit code 0")
        run = MagicMock()
        passed, msg = engine.verify_step(run, step, "Build complete. exit code 0")
        assert passed

    def test_verify_step_fast_path_nonzero_exit(self, tmp_path):
        """Verification fast-path detects non-zero exit code."""
        engine = make_engine(tmp_path)
        step = WorkflowStep(id="build", agent="dev", prompt="build", expects="exit code 0")
        run = MagicMock()
        passed, msg = engine.verify_step(run, step, "Build failed. exit code 1")
        assert not passed

    def test_verify_step_llm_based(self, tmp_path):
        """Verification falls back to LLM when no fast-path match."""
        call_fn = MagicMock(return_value="NO\nThe output does not match.")
        engine = make_engine(tmp_path, call_fn=call_fn)
        step = WorkflowStep(id="s1", agent="dev", prompt="do it", expects="Has documentation")
        run = MagicMock()
        passed, msg = engine.verify_step(run, step, "Here is code with no docs")
        assert not passed

    def test_save_and_load_run(self, tmp_path):
        """Workflow runs persist to disk and can be listed."""
        engine = make_engine(tmp_path)
        run = WorkflowRun(
            workflow_name="test",
            trigger_text="build something",
            steps=[WorkflowStep(id="s1", agent="dev", prompt="do it")],
            step_results={"s1": StepResult(status="passed", output="done")},
            status="completed",
        )
        engine.save_run(run)

        completed = engine.list_completed_runs()
        assert len(completed) >= 1
        assert any("test" in f for f in completed)

    def test_load_run_from_file(self, tmp_path):
        """Saved run can be loaded back."""
        engine = make_engine(tmp_path)
        run = WorkflowRun(
            workflow_name="deploy",
            trigger_text="deploy now",
            steps=[WorkflowStep(id="s1", agent="devops", prompt="deploy")],
            step_results={"s1": StepResult(status="passed", output="deployed")},
            status="completed",
        )
        engine.save_run(run)

        files = engine.list_completed_runs()
        loaded = engine.load_run(os.path.join(engine.data_dir, files[0]))
        assert loaded is not None
        assert loaded.workflow_name == "deploy"
        assert loaded.step_results["s1"].status == "passed"

    def test_load_run_missing_file(self, tmp_path):
        """Loading nonexistent file returns None."""
        engine = make_engine(tmp_path)
        result = engine.load_run("/nonexistent/path.json")
        assert result is None

    def test_get_active_runs(self, tmp_path):
        """Active runs are tracked."""
        engine = make_engine(tmp_path, workflows={
            "flow": {"steps": [{"id": "s1", "agent": "dev", "prompt": "do it"}]}
        })
        assert engine.get_active_runs() == []
        run = engine.start_workflow("flow", "test")
        assert len(engine.get_active_runs()) == 1
        assert engine.get_active_runs()[0].run_id == run.run_id
