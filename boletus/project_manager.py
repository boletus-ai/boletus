"""Multi-project context persistence and switching."""

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)


class ProjectManager:
    """Manages multiple projects with context persistence for seamless switching."""

    def __init__(self, projects_config: dict, data_dir: str):
        self.projects = projects_config or {}
        os.makedirs(data_dir, exist_ok=True)
        self.state_file = os.path.join(data_dir, "project_state.json")
        self.context_dir = os.path.join(data_dir, "project_contexts")
        os.makedirs(self.context_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"active_project": None, "status": "idle"}

    def _save_state(self, state: dict):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.state_file)

    def get_active_project(self) -> str | None:
        with self._lock:
            state = self._load_state()
            return state.get("active_project")

    def get_project_context(self) -> str:
        with self._lock:
            state = self._load_state()
            project_key = state.get("active_project")
            if not project_key or project_key not in self.projects:
                return ""
            return self.projects[project_key].get("context", "")

    def get_project_codebase(self) -> str | None:
        """Get codebase path for the active project."""
        with self._lock:
            state = self._load_state()
            project_key = state.get("active_project")
            if not project_key or project_key not in self.projects:
                return None
            return self.projects[project_key].get("codebase")

    def get_project_info(self, project_key: str) -> dict | None:
        return self.projects.get(project_key)

    def start_project(self, project_key: str) -> bool:
        if project_key not in self.projects:
            logger.warning(f"Unknown project: {project_key}")
            return False
        with self._lock:
            state = self._load_state()
            state["active_project"] = project_key
            state["status"] = "active"
            self._save_state(state)
            logger.info(f"Project activated: {project_key}")
            return True

    def stop_project(self) -> str | None:
        with self._lock:
            state = self._load_state()
            prev = state.get("active_project")
            state["active_project"] = None
            state["status"] = "idle"
            self._save_state(state)
            if prev:
                logger.info(f"Project stopped: {prev}")
            return prev

    def is_active(self) -> bool:
        with self._lock:
            state = self._load_state()
            return state.get("active_project") is not None

    def get_status(self) -> str:
        with self._lock:
            state = self._load_state()
            project_key = state.get("active_project")
            if not project_key:
                return "IDLE — No active project."
            project = self.projects.get(project_key, {})
            return f"ACTIVE — Working on {project.get('name', project_key)}: {project.get('description', '')}"

    def list_projects(self) -> str:
        lines = ["Available projects:"]
        for key, proj in self.projects.items():
            lines.append(f"  {key} — {proj.get('name', key)}: {proj.get('description', '')}")
        return "\n".join(lines)

    # --- Per-project context persistence ---

    def save_project_context(self, project_key: str, context: str):
        fpath = os.path.join(self.context_dir, f"{project_key}.md")
        tmp = fpath + ".tmp"
        with open(tmp, "w") as f:
            f.write(context)
        os.replace(tmp, fpath)
        logger.info(f"Saved context for project: {project_key} ({len(context)} chars)")

    def load_project_context_file(self, project_key: str) -> str:
        """Load saved working context for a project."""
        fpath = os.path.join(self.context_dir, f"{project_key}.md")
        try:
            with open(fpath) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def save_context_on_switch(self, old_project: str | None, context: str):
        if old_project and context.strip():
            self.save_project_context(old_project, context)
