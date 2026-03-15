"""Safety guardrails — circuit breaker and execution guard for agents."""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class GuardError(Exception):
    """Raised when an agent is not allowed to execute."""
    pass


class CircuitBrokenError(GuardError):
    """Raised when an agent's circuit breaker has tripped."""

    def __init__(self, agent_name: str, last_error: str):
        self.agent_name = agent_name
        self.last_error = last_error
        super().__init__(f"Circuit breaker tripped for {agent_name}: {last_error}")


class CircuitBreaker:
    """Tracks agent failures and pauses broken agents.

    After ``max_failures`` consecutive failures within the failure window,
    the circuit trips and the agent is paused for ``reset_after`` seconds.
    A single success resets the failure counter.
    """

    def __init__(self, max_failures: int = 3, reset_after: int = 600):
        self.max_failures = max_failures
        self.reset_after = reset_after  # seconds
        self._failures: dict[str, list[float]] = {}  # agent -> [timestamps]
        self._tripped: dict[str, float] = {}  # agent -> tripped_at
        self._lock = threading.Lock()

    def record_success(self, agent_name: str):
        """Reset failure count on success."""
        with self._lock:
            self._failures.pop(agent_name, None)
            self._tripped.pop(agent_name, None)

    def record_failure(self, agent_name: str) -> bool:
        """Record a failure. Returns True if circuit is now tripped."""
        with self._lock:
            now = time.time()
            failures = self._failures.setdefault(agent_name, [])
            failures.append(now)

            # Only consider recent failures (within reset_after window)
            cutoff = now - self.reset_after
            self._failures[agent_name] = [t for t in failures if t > cutoff]

            if len(self._failures[agent_name]) >= self.max_failures:
                self._tripped[agent_name] = now
                logger.warning(
                    f"Circuit breaker TRIPPED for {agent_name} "
                    f"({len(self._failures[agent_name])} failures in {self.reset_after}s)"
                )
                return True
            return False

    def is_tripped(self, agent_name: str) -> bool:
        """Check if agent is paused. Auto-resets after reset_after seconds."""
        with self._lock:
            tripped_at = self._tripped.get(agent_name)
            if tripped_at is None:
                return False
            if time.time() - tripped_at > self.reset_after:
                # Auto-reset
                logger.info(f"Circuit breaker auto-reset for {agent_name}")
                self._tripped.pop(agent_name, None)
                self._failures.pop(agent_name, None)
                return False
            return True

    def get_status(self) -> dict[str, dict]:
        """Return status of all tracked agents for reporting."""
        with self._lock:
            now = time.time()
            status = {}
            all_agents = set(self._failures.keys()) | set(self._tripped.keys())
            for agent_name in all_agents:
                tripped_at = self._tripped.get(agent_name)
                # Inline read-only tripped check (don't call is_tripped which mutates)
                is_currently_tripped = (
                    tripped_at is not None
                    and (now - tripped_at) <= self.reset_after
                )
                failures = self._failures.get(agent_name, [])
                entry: dict = {
                    "tripped": is_currently_tripped,
                    "recent_failures": len(failures),
                    "max_failures": self.max_failures,
                }
                if is_currently_tripped and tripped_at is not None:
                    remaining = self.reset_after - (now - tripped_at)
                    entry["resets_in_seconds"] = max(0, int(remaining))
                status[agent_name] = entry
            return status


class ExecutionGuard:
    """Pre-execution safety checks wrapping agent calls."""

    def __init__(self, circuit_breaker: CircuitBreaker, agent_names: set[str] | None = None):
        self.circuit_breaker = circuit_breaker
        self._known_agents: set[str] = agent_names or set()

    def set_known_agents(self, agent_names: set[str]):
        """Update the set of known agent names."""
        self._known_agents = agent_names

    def can_execute(self, agent_name: str) -> tuple[bool, str]:
        """Check if agent is allowed to execute. Returns (allowed, reason)."""
        if self._known_agents and agent_name not in self._known_agents:
            return False, f"Unknown agent: {agent_name}"

        if self.circuit_breaker.is_tripped(agent_name):
            status = self.circuit_breaker.get_status().get(agent_name, {})
            resets_in = status.get("resets_in_seconds", "?")
            return False, (
                f"Circuit breaker tripped for {agent_name}. "
                f"Resets in {resets_in}s."
            )

        return True, ""

    def wrap_execution(self, agent_name: str, fn, *args, **kwargs):
        """Wrap an agent call with safety tracking.

        Checks ``can_execute`` first, then calls ``fn``. On success the
        failure counter is reset; on failure it is incremented. If the
        circuit trips, ``CircuitBrokenError`` is raised so the caller can
        alert the owner.
        """
        allowed, reason = self.can_execute(agent_name)
        if not allowed:
            raise GuardError(reason)

        try:
            result = fn(*args, **kwargs)
            self.circuit_breaker.record_success(agent_name)
            return result
        except Exception as e:
            tripped = self.circuit_breaker.record_failure(agent_name)
            if tripped:
                raise CircuitBrokenError(agent_name, str(e)) from e
            raise
