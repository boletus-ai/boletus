"""Agent memory & learning — structured persistence, shared knowledge, repo maps."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

SECTIONS = ["Decisions", "Lessons Learned", "Active Context", "Task Log"]

# Cache for generate_repo_map
_repo_map_cache: dict[str, tuple[float, str]] = {}


def parse_structured_memory(content: str) -> dict[str, list[str]]:
    """Parse markdown with ## Section headers into a dict.

    Each section collects all lines until the next ## header.
    Returns a dict mapping section name -> list of entries (non-empty lines).
    """
    sections: dict[str, list[str]] = {s: [] for s in SECTIONS}
    current_section: str | None = None

    for line in content.splitlines():
        header_match = re.match(r"^##\s+(.+)$", line)
        if header_match:
            name = header_match.group(1).strip()
            if name in sections:
                current_section = name
            else:
                current_section = None
            continue
        if current_section is not None and line.strip():
            sections[current_section].append(line)

    return sections


def _ensure_memory_file(memory_file: str) -> None:
    """Create a memory file with section headers if it doesn't exist."""
    if os.path.exists(memory_file):
        return
    os.makedirs(os.path.dirname(memory_file), exist_ok=True)
    with open(memory_file, "w") as f:
        for section in SECTIONS:
            f.write(f"## {section}\n\n")


def append_to_section(agent_name: str, memory_dir: str, section: str, entry: str) -> None:
    """Append a timestamped entry to a specific section of agent memory.

    Auto-creates the file with section headers if it doesn't exist.
    Task Log section keeps only last 20 entries. Others keep all.
    """
    if section not in SECTIONS:
        logger.warning(f"Unknown memory section '{section}', defaulting to Task Log")
        section = "Task Log"

    os.makedirs(memory_dir, exist_ok=True)
    memory_file = os.path.join(memory_dir, f"{agent_name}.md")
    _ensure_memory_file(memory_file)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    timestamped_entry = f"- [{timestamp}] {entry}"

    try:
        with open(memory_file) as f:
            content = f.read()
    except OSError as e:
        logger.error(f"Failed to read memory for {agent_name}: {e}")
        return

    sections = parse_structured_memory(content)
    sections[section].append(timestamped_entry)

    # Task Log: keep only last 20 entries
    if section == "Task Log" and len(sections["Task Log"]) > 20:
        sections["Task Log"] = sections["Task Log"][-20:]

    # Rebuild the file
    try:
        with open(memory_file, "w") as f:
            for sec_name in SECTIONS:
                f.write(f"## {sec_name}\n")
                entries = sections.get(sec_name, [])
                for e in entries:
                    f.write(f"{e}\n")
                f.write("\n")
    except OSError as e:
        logger.error(f"Failed to write memory for {agent_name}: {e}")


def build_memory_prompt(agent_name: str, memory_dir: str, max_chars: int = 8000) -> str:
    """Build a compact memory string for prompt injection.

    Priority: Decisions > Active Context > Lessons > Task Log.
    Truncates least important sections first to fit budget.
    """
    memory_file = os.path.join(memory_dir, f"{agent_name}.md")
    try:
        with open(memory_file) as f:
            content = f.read()
    except FileNotFoundError:
        return ""

    if not content.strip():
        return ""

    sections = parse_structured_memory(content)

    # Priority order (highest first)
    priority_order = ["Decisions", "Active Context", "Lessons Learned", "Task Log"]
    # Build output, truncating lowest priority first
    parts: list[tuple[str, str]] = []
    for sec_name in priority_order:
        entries = sections.get(sec_name, [])
        if entries:
            parts.append((sec_name, "\n".join(entries)))

    if not parts:
        return ""

    # Try full output first
    result = _format_memory_parts(parts)
    if len(result) <= max_chars:
        return result

    # Truncate from lowest priority (reverse of priority_order)
    for i in range(len(parts) - 1, -1, -1):
        sec_name, sec_text = parts[i]
        # Calculate how much we need to cut
        excess = len(result) - max_chars
        if excess <= 0:
            break
        if len(sec_text) > excess + 50:
            # Partial truncation
            parts[i] = (sec_name, sec_text[: len(sec_text) - excess - 20] + "\n... [truncated]")
        else:
            # Remove entire section
            parts[i] = (sec_name, "")
        result = _format_memory_parts([(n, t) for n, t in parts if t])

    return result[:max_chars]


def _format_memory_parts(parts: list[tuple[str, str]]) -> str:
    """Format memory sections into a string."""
    lines = []
    for name, text in parts:
        if text:
            lines.append(f"### {name}\n{text}")
    return "\n\n".join(lines)


def load_shared_knowledge(memory_dir: str, max_chars: int = 4000) -> str:
    """Read memory/_shared.md and truncate to max_chars."""
    shared_file = os.path.join(memory_dir, "_shared.md")
    try:
        with open(shared_file) as f:
            content = f.read()
    except FileNotFoundError:
        return ""

    if len(content) > max_chars:
        content = content[:max_chars] + "\n... [truncated]"
    return content


def generate_repo_map(codebase_path: str, max_chars: int = 3000) -> str:
    """Walk the project directory and produce a compact tree with file sizes.

    Skip: .git, node_modules, .venv, __pycache__, data, memory, context.
    Format: 'src/api/auth.py (2.1KB)' per file. Cache for 5 minutes.
    """
    if not codebase_path or not os.path.isdir(codebase_path):
        return ""

    cache_key = codebase_path
    now = time.time()
    cached = _repo_map_cache.get(cache_key)
    if cached and (now - cached[0]) < 300:
        return cached[1]

    skip_dirs = {".git", "node_modules", ".venv", "__pycache__", "data", "memory", "context"}
    lines: list[str] = []

    for root, dirs, files in os.walk(codebase_path):
        # Filter out skipped directories in-place
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                continue
            rel = os.path.relpath(fpath, codebase_path)
            if size >= 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size}B"
            lines.append(f"{rel} ({size_str})")

    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... [truncated]"

    _repo_map_cache[cache_key] = (now, result)
    return result


def log_decision(data_dir: str, agent: str, decision: str, rationale: str = "") -> None:
    """Append a decision to data/decisions.jsonl."""
    os.makedirs(data_dir, exist_ok=True)
    filepath = os.path.join(data_dir, "decisions.jsonl")
    entry = {
        "timestamp": datetime.now().isoformat(),
        "agent": agent,
        "decision": decision,
        "rationale": rationale,
    }
    try:
        with open(filepath, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.error(f"Failed to log decision: {e}")


def get_recent_decisions(data_dir: str, limit: int = 10) -> str:
    """Return recent decisions formatted as text for prompt injection."""
    filepath = os.path.join(data_dir, "decisions.jsonl")
    try:
        with open(filepath) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return ""

    recent = lines[-limit:]
    parts: list[str] = []
    for line in recent:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = entry.get("timestamp", "?")[:16]
            agent = entry.get("agent", "?")
            decision = entry.get("decision", "")
            rationale = entry.get("rationale", "")
            text = f"- [{ts}] {agent}: {decision}"
            if rationale:
                text += f" (reason: {rationale})"
            parts.append(text)
        except json.JSONDecodeError:
            continue

    return "\n".join(parts)
