"""Tests for boletus.guardrails — circuit breaker and execution guard."""

import time
import pytest
from unittest.mock import MagicMock

from boletus.guardrails import CircuitBreaker, ExecutionGuard, CircuitBrokenError, GuardError


class TestCircuitBreaker:
    def test_not_tripped_initially(self):
        cb = CircuitBreaker(max_failures=3)
        assert not cb.is_tripped("agent1")

    def test_trips_after_max_failures(self):
        cb = CircuitBreaker(max_failures=3)
        cb.record_failure("agent1")
        cb.record_failure("agent1")
        tripped = cb.record_failure("agent1")
        assert tripped
        assert cb.is_tripped("agent1")

    def test_does_not_trip_below_max(self):
        cb = CircuitBreaker(max_failures=3)
        assert not cb.record_failure("agent1")
        assert not cb.record_failure("agent1")
        assert not cb.is_tripped("agent1")

    def test_success_resets_failures(self):
        cb = CircuitBreaker(max_failures=3)
        cb.record_failure("agent1")
        cb.record_failure("agent1")
        cb.record_success("agent1")
        tripped = cb.record_failure("agent1")
        assert not tripped  # reset, so only 1 failure now

    def test_auto_reset_after_timeout(self):
        cb = CircuitBreaker(max_failures=1, reset_after=0.1)
        cb.record_failure("agent1")
        assert cb.is_tripped("agent1")
        time.sleep(0.15)
        assert not cb.is_tripped("agent1")

    def test_different_agents_independent(self):
        cb = CircuitBreaker(max_failures=2)
        cb.record_failure("agent1")
        cb.record_failure("agent1")
        assert cb.is_tripped("agent1")
        assert not cb.is_tripped("agent2")

    def test_get_status(self):
        cb = CircuitBreaker(max_failures=3)
        cb.record_failure("agent1")
        status = cb.get_status()
        assert "agent1" in status
        assert status["agent1"]["recent_failures"] == 1
        assert status["agent1"]["tripped"] is False

    def test_get_status_tripped(self):
        cb = CircuitBreaker(max_failures=1)
        cb.record_failure("agent1")
        status = cb.get_status()
        assert status["agent1"]["tripped"] is True
        assert "resets_in_seconds" in status["agent1"]


class TestExecutionGuard:
    def test_allows_healthy_agent(self):
        guard = ExecutionGuard(CircuitBreaker(max_failures=3))
        allowed, reason = guard.can_execute("agent1")
        assert allowed
        assert reason == ""

    def test_blocks_tripped_agent(self):
        cb = CircuitBreaker(max_failures=1)
        cb.record_failure("agent1")
        guard = ExecutionGuard(cb)
        allowed, reason = guard.can_execute("agent1")
        assert not allowed
        assert "tripped" in reason.lower()

    def test_blocks_unknown_agent(self):
        guard = ExecutionGuard(CircuitBreaker(), agent_names={"dev", "cto"})
        allowed, reason = guard.can_execute("unknown_agent")
        assert not allowed
        assert "Unknown agent" in reason

    def test_allows_known_agent(self):
        guard = ExecutionGuard(CircuitBreaker(), agent_names={"dev", "cto"})
        allowed, reason = guard.can_execute("dev")
        assert allowed

    def test_wrap_execution_success(self):
        guard = ExecutionGuard(CircuitBreaker(max_failures=3))
        result = guard.wrap_execution("agent1", lambda: "ok")
        assert result == "ok"

    def test_wrap_execution_records_success(self):
        cb = CircuitBreaker(max_failures=3)
        cb.record_failure("agent1")
        cb.record_failure("agent1")
        guard = ExecutionGuard(cb)
        guard.wrap_execution("agent1", lambda: "ok")
        # Success should have reset failures
        assert not cb.is_tripped("agent1")

    def test_wrap_execution_raises_on_circuit_break(self):
        cb = CircuitBreaker(max_failures=1)
        guard = ExecutionGuard(cb)
        with pytest.raises(CircuitBrokenError):
            guard.wrap_execution("agent1", MagicMock(side_effect=Exception("fail")))

    def test_wrap_execution_reraises_before_trip(self):
        """If failure doesn't trip the breaker, the original exception propagates."""
        cb = CircuitBreaker(max_failures=3)
        guard = ExecutionGuard(cb)
        with pytest.raises(Exception, match="oops"):
            guard.wrap_execution("agent1", MagicMock(side_effect=Exception("oops")))

    def test_wrap_execution_guard_error_on_tripped(self):
        """wrap_execution raises GuardError if agent already tripped."""
        cb = CircuitBreaker(max_failures=1)
        cb.record_failure("agent1")
        guard = ExecutionGuard(cb)
        with pytest.raises(GuardError):
            guard.wrap_execution("agent1", lambda: "ok")

    def test_set_known_agents(self):
        guard = ExecutionGuard(CircuitBreaker())
        guard.set_known_agents({"dev"})
        allowed, _ = guard.can_execute("dev")
        assert allowed
        allowed, _ = guard.can_execute("unknown")
        assert not allowed
