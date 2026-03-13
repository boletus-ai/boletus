"""Task board for cross-agent coordination."""

import json
import logging
import os
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TaskManager:
    """Thread-safe JSON task board with atomic operations and stuck task recovery."""

    def __init__(self, data_dir: str, stuck_timeout_minutes: int = 10, archive_after_days: int = 30):
        os.makedirs(data_dir, exist_ok=True)
        self.tasks_file = os.path.join(data_dir, "tasks.json")
        self.archive_file = os.path.join(data_dir, "tasks_archive.json")
        self.stuck_timeout_minutes = stuck_timeout_minutes
        self.archive_after_days = archive_after_days
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            with open(self.tasks_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, tasks: list[dict]):
        tmp = self.tasks_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.tasks_file)

    def _load_archive(self) -> list[dict]:
        try:
            with open(self.archive_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_archive(self, tasks: list[dict]):
        tmp = self.archive_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.archive_file)

    def add_task(
        self, title: str, assigned_to: str, created_by: str,
        priority: str = "medium", details: str = "",
    ) -> dict | None:
        if not title or len(title.strip()) < 5:
            logger.warning(f"Rejected task with invalid title: {title!r}")
            return None
        with self._lock:
            tasks = self._load()
            task = {
                "id": max((t["id"] for t in tasks), default=0) + 1,
                "title": title,
                "assigned_to": assigned_to,
                "created_by": created_by,
                "priority": priority,
                "status": "todo",
                "details": details,
                "created_at": datetime.now().isoformat(),
                "completed_at": None,
            }
            tasks.append(task)
            self._save(tasks)
            return task

    def claim_task(self, assigned_to: str) -> dict | None:
        """Atomically claim the next todo task. Also recovers stuck tasks."""
        with self._lock:
            tasks = self._load()
            now = datetime.now()

            # Recover stuck tasks
            for t in tasks:
                if t["status"] == "in_progress" and t.get("started_at"):
                    try:
                        started = datetime.fromisoformat(t["started_at"])
                        if (now - started) > timedelta(minutes=self.stuck_timeout_minutes):
                            t["status"] = "todo"
                            t.pop("started_at", None)
                    except (ValueError, TypeError):
                        pass

            # Claim next task sorted by priority
            priority_order = {"high": 0, "medium": 1, "low": 2}
            candidates = sorted(
                [t for t in tasks if t["assigned_to"] == assigned_to and t["status"] == "todo"],
                key=lambda t: priority_order.get(t.get("priority", "medium"), 1),
            )
            if candidates:
                chosen = candidates[0]
                for t in tasks:
                    if t["id"] == chosen["id"]:
                        t["status"] = "in_progress"
                        t["started_at"] = now.isoformat()
                        t["claim_generation"] = t.get("claim_generation", 0) + 1
                        self._save(tasks)
                        return t

            self._save(tasks)
            return None

    def get_task_by_id(self, task_id: int) -> dict | None:
        with self._lock:
            tasks = self._load()
            for t in tasks:
                if t["id"] == task_id:
                    return dict(t)
            return None

    def complete_task(self, task_id: int, result: str = "") -> dict | None:
        with self._lock:
            tasks = self._load()
            for t in tasks:
                if t["id"] == task_id:
                    t["status"] = "done"
                    t["completed_at"] = datetime.now().isoformat()
                    t["result"] = result
                    self._save(tasks)
                    return t
            return None

    def reset_task(self, task_id: int) -> dict | None:
        """Reset a task back to todo (e.g. after failed verification)."""
        with self._lock:
            tasks = self._load()
            for t in tasks:
                if t["id"] == task_id:
                    t["status"] = "todo"
                    t.pop("claimed_by", None)
                    t.pop("claimed_at", None)
                    t["claim_generation"] = t.get("claim_generation", 0) + 1
                    self._save(tasks)
                    logger.info(f"Task #{task_id} reset to todo")
                    return t
            return None

    def cancel_task(self, task_id: int, reason: str = "") -> dict | None:
        with self._lock:
            tasks = self._load()
            for t in tasks:
                if t["id"] == task_id and t["status"] in ("todo", "in_progress"):
                    t["status"] = "cancelled"
                    t["completed_at"] = datetime.now().isoformat()
                    t["result"] = f"Cancelled: {reason}" if reason else "Cancelled"
                    self._save(tasks)
                    logger.info(f"Task #{task_id} cancelled: {reason}")
                    return t
            return None

    def has_pending_work(self, assigned_to: str) -> bool:
        with self._lock:
            tasks = self._load()
            return any(
                t["assigned_to"] == assigned_to and t["status"] in ("todo", "in_progress")
                for t in tasks
            )

    def count_open_tasks(self) -> int:
        with self._lock:
            tasks = self._load()
            return sum(1 for t in tasks if t["status"] in ("todo", "in_progress"))

    def get_tasks(self, assigned_to: str | None = None, status: str | None = None) -> list[dict]:
        with self._lock:
            tasks = self._load()
            if assigned_to:
                tasks = [t for t in tasks if t["assigned_to"] == assigned_to]
            if status:
                tasks = [t for t in tasks if t["status"] == status]
            return tasks

    def get_summary(self, include_done: bool = False) -> str:
        with self._lock:
            tasks = self._load()
            if not tasks:
                return "No tasks."
            lines = []
            done_count = 0
            for t in tasks:
                if t["status"] == "done":
                    done_count += 1
                    if not include_done:
                        continue
                icon = {"done": "[DONE]", "in_progress": "[WIP]", "cancelled": "[X]"}.get(t["status"], "[TODO]")
                lines.append(
                    f"{icon} #{t['id']} [{t['priority'].upper()}] {t['title']} "
                    f"-> {t['assigned_to']} (from {t['created_by']})"
                )
            if not include_done and done_count > 0:
                lines.append(f"\n({done_count} completed tasks hidden)")
            return "\n".join(lines) if lines else "No open tasks."

    def get_stuck_tasks(self) -> list[dict]:
        """Return tasks stuck in_progress for longer than 2x stuck_timeout."""
        with self._lock:
            tasks = self._load()
            now = datetime.now()
            threshold = timedelta(minutes=self.stuck_timeout_minutes * 2)
            stuck = []
            for t in tasks:
                if t["status"] == "in_progress" and t.get("started_at"):
                    try:
                        started = datetime.fromisoformat(t["started_at"])
                        if (now - started) > threshold:
                            stuck.append(dict(t))
                    except (ValueError, TypeError):
                        pass
            return stuck

    def archive_old_tasks(self) -> int:
        with self._lock:
            tasks = self._load()
            now = datetime.now()
            keep = []
            to_archive = []

            for t in tasks:
                if t["status"] == "done" and t.get("completed_at"):
                    try:
                        completed = datetime.fromisoformat(t["completed_at"])
                        if (now - completed) > timedelta(days=self.archive_after_days):
                            to_archive.append(t)
                            continue
                    except (ValueError, TypeError):
                        pass
                keep.append(t)

            if to_archive:
                archive = self._load_archive()
                archive.extend(to_archive)
                self._save_archive(archive)
                self._save(keep)
                logger.info(f"Archived {len(to_archive)} old tasks")

            return len(to_archive)
