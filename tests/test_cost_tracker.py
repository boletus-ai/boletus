"""Tests for cost_tracker module."""

from crewmatic.cost_tracker import CostTracker


def test_record_and_summary(tmp_path):
    tracker = CostTracker(str(tmp_path))
    tracker.record_call("ceo", "opus")
    tracker.record_call("cmo", "sonnet")
    tracker.record_call("ceo", "opus")

    stats = tracker.get_stats()
    assert stats["total_calls"] == 3
    assert stats["agents"]["ceo"]["calls"] == 2
    assert stats["agents"]["cmo"]["calls"] == 1


def test_persistence(tmp_path):
    tracker1 = CostTracker(str(tmp_path))
    tracker1.record_call("cto", "opus")

    tracker2 = CostTracker(str(tmp_path))
    assert tracker2.get_stats()["total_calls"] == 1
    assert tracker2.get_stats()["agents"]["cto"]["calls"] == 1


def test_daily_cost(tmp_path):
    tracker = CostTracker(str(tmp_path))
    tracker.record_call("ceo", "opus")
    assert tracker.get_daily_cost() > 0


def test_summary_format(tmp_path):
    tracker = CostTracker(str(tmp_path))
    tracker.record_call("ceo", "opus")
    summary = tracker.get_summary()
    assert "Total calls: 1" in summary
    assert "CEO" in summary


def test_empty_tracker(tmp_path):
    tracker = CostTracker(str(tmp_path))
    assert tracker.get_stats()["total_calls"] == 0
    assert tracker.get_daily_cost() == 0.0
