"""Lightweight cost tracking — counts agent calls and estimates spend."""

import json
import logging
import os
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Rough token cost estimates per model (input + output per call avg)
# These are estimates — actual costs depend on prompt/response length
MODEL_COST_PER_CALL = {
    "opus": 0.30,      # ~$0.30 per typical agent call (15k input + 4k output tokens)
    "sonnet": 0.05,    # ~$0.05 per typical agent call
    "haiku": 0.005,    # ~$0.005 per typical agent call
}


class CostTracker:
    """Track agent call counts and estimated costs.

    Persists to a JSON file in the data directory so costs survive restarts.
    Thread-safe for concurrent agent calls.
    """

    def __init__(self, data_dir: str):
        self._lock = threading.Lock()
        self.data_dir = data_dir
        self.stats_file = os.path.join(data_dir, "cost_stats.json")
        self._stats = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt cost_stats.json — starting fresh")
        return {"agents": {}, "daily": {}, "total_calls": 0, "total_estimated_cost": 0.0}

    def _save(self):
        os.makedirs(self.data_dir, exist_ok=True)
        tmp_file = self.stats_file + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump(self._stats, f, indent=2)
            os.replace(tmp_file, self.stats_file)
        except OSError as e:
            logger.error(f"Failed to save cost stats: {e}")

    def record_call(self, agent_name: str, model: str):
        """Record a single agent call."""
        cost = MODEL_COST_PER_CALL.get(model, 0.05)
        today = datetime.now().strftime("%Y-%m-%d")

        with self._lock:
            # Per-agent stats
            if agent_name not in self._stats["agents"]:
                self._stats["agents"][agent_name] = {"calls": 0, "estimated_cost": 0.0}
            self._stats["agents"][agent_name]["calls"] += 1
            self._stats["agents"][agent_name]["estimated_cost"] += cost

            # Daily stats
            if today not in self._stats["daily"]:
                self._stats["daily"][today] = {"calls": 0, "estimated_cost": 0.0}
            self._stats["daily"][today]["calls"] += 1
            self._stats["daily"][today]["estimated_cost"] += cost

            # Totals
            self._stats["total_calls"] += 1
            self._stats["total_estimated_cost"] += cost

            self._save()

    def get_summary(self) -> str:
        """Return a human-readable cost summary."""
        with self._lock:
            s = self._stats
            lines = [f"Total calls: {s['total_calls']} | Estimated cost: ${s['total_estimated_cost']:.2f}"]

            if s["agents"]:
                lines.append("\nPer agent:")
                for name, data in sorted(s["agents"].items()):
                    lines.append(f"  {name.upper()}: {data['calls']} calls (${data['estimated_cost']:.2f})")

            # Last 7 days
            daily = sorted(s.get("daily", {}).items(), reverse=True)[:7]
            if daily:
                lines.append("\nLast 7 days:")
                for day, data in daily:
                    lines.append(f"  {day}: {data['calls']} calls (${data['estimated_cost']:.2f})")

            return "\n".join(lines)

    def get_daily_cost(self) -> float:
        """Return today's estimated cost."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            return self._stats.get("daily", {}).get(today, {}).get("estimated_cost", 0.0)

    def get_stats(self) -> dict:
        """Return raw stats dict."""
        with self._lock:
            return dict(self._stats)
