"""Tests for TaskManager."""

import os
import tempfile

from crewmatic.task_manager import TaskManager


def make_tm(tmp_path):
    return TaskManager(data_dir=str(tmp_path))


def test_add_and_claim(tmp_path):
    tm = make_tm(tmp_path)
    task = tm.add_task("Build the login page", assigned_to="dev", created_by="lead")
    assert task is not None
    assert task["status"] == "todo"

    claimed = tm.claim_task("dev")
    assert claimed is not None
    assert claimed["id"] == task["id"]
    assert claimed["status"] == "in_progress"


def test_claim_returns_none_when_empty(tmp_path):
    tm = make_tm(tmp_path)
    assert tm.claim_task("dev") is None


def test_complete_task(tmp_path):
    tm = make_tm(tmp_path)
    task = tm.add_task("Fix the bug in auth", assigned_to="dev", created_by="lead")
    tm.claim_task("dev")
    result = tm.complete_task(task["id"], result="Fixed by adding null check")
    assert result["status"] == "done"
    assert result["result"] == "Fixed by adding null check"


def test_cancel_task(tmp_path):
    tm = make_tm(tmp_path)
    task = tm.add_task("Remove old endpoint", assigned_to="dev", created_by="lead")
    result = tm.cancel_task(task["id"], reason="No longer needed")
    assert result["status"] == "cancelled"


def test_priority_ordering(tmp_path):
    tm = make_tm(tmp_path)
    tm.add_task("Low priority task here", assigned_to="dev", created_by="lead", priority="low")
    tm.add_task("High priority task here", assigned_to="dev", created_by="lead", priority="high")
    tm.add_task("Medium priority task here", assigned_to="dev", created_by="lead", priority="medium")

    claimed = tm.claim_task("dev")
    assert claimed["priority"] == "high"


def test_rejects_short_title(tmp_path):
    tm = make_tm(tmp_path)
    assert tm.add_task("hi", assigned_to="dev", created_by="lead") is None


def test_count_open_tasks(tmp_path):
    tm = make_tm(tmp_path)
    tm.add_task("Task one for counting", assigned_to="dev", created_by="lead")
    tm.add_task("Task two for counting", assigned_to="dev", created_by="lead")
    assert tm.count_open_tasks() == 2


def test_get_summary(tmp_path):
    tm = make_tm(tmp_path)
    assert tm.get_summary() == "No tasks."
    tm.add_task("Write unit tests for auth", assigned_to="dev", created_by="lead")
    summary = tm.get_summary()
    assert "Write unit tests" in summary
