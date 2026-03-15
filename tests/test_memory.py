"""Tests for agent memory persistence — append_agent_memory and structured memory."""

import json
import os

from boletus.context import append_agent_memory, load_agent_memory
from boletus.memory import (
    append_to_section,
    build_memory_prompt,
    generate_repo_map,
    get_recent_decisions,
    load_shared_knowledge,
    log_decision,
    parse_structured_memory,
    SECTIONS,
)


def test_append_creates_file(tmp_path):
    append_agent_memory("ceo", str(tmp_path), "First planning session")
    content = load_agent_memory("ceo", str(tmp_path))
    assert "First planning session" in content
    assert "Task Log" in content  # uses structured sections


def test_append_accumulates(tmp_path):
    append_agent_memory("cto", str(tmp_path), "Built API endpoint")
    append_agent_memory("cto", str(tmp_path), "Fixed auth bug")
    content = load_agent_memory("cto", str(tmp_path))
    assert "Built API endpoint" in content
    assert "Fixed auth bug" in content


def test_append_preserves_existing_sections(tmp_path):
    """Existing structured memory content is preserved across appends."""
    mem_file = tmp_path / "dev.md"
    mem_file.write_text("## Decisions\n- Use functional style\n\n## Lessons Learned\n\n## Active Context\n\n## Task Log\n\n")

    append_agent_memory("dev", str(tmp_path), "Completed task #5")
    content = mem_file.read_text()
    assert "Use functional style" in content
    assert "Completed task #5" in content


def test_append_creates_directory(tmp_path):
    deep = str(tmp_path / "nested" / "memory")
    append_agent_memory("cmo", deep, "Market research done")
    assert os.path.exists(os.path.join(deep, "cmo.md"))


def test_task_log_keeps_last_20(tmp_path):
    """Task Log section trims to last 20 entries."""
    for i in range(25):
        append_agent_memory("agent", str(tmp_path), f"Entry {i}")
    content = (tmp_path / "agent.md").read_text()
    sections = parse_structured_memory(content)
    assert len(sections["Task Log"]) == 20
    assert "Entry 24" in sections["Task Log"][-1]
    assert "Entry 4" not in "\n".join(sections["Task Log"])


# --- Structured memory tests ---

def test_parse_structured_memory():
    content = "## Decisions\n- chose FastAPI\n\n## Lessons Learned\n- always test\n\n## Active Context\n\n## Task Log\n- did stuff\n"
    sections = parse_structured_memory(content)
    assert len(sections["Decisions"]) == 1
    assert "FastAPI" in sections["Decisions"][0]
    assert len(sections["Lessons Learned"]) == 1
    assert len(sections["Active Context"]) == 0
    assert len(sections["Task Log"]) == 1


def test_append_to_section(tmp_path):
    append_to_section("ceo", str(tmp_path), "Decisions", "Use PostgreSQL")
    content = (tmp_path / "ceo.md").read_text()
    sections = parse_structured_memory(content)
    assert any("Use PostgreSQL" in e for e in sections["Decisions"])


def test_build_memory_prompt_empty(tmp_path):
    result = build_memory_prompt("nobody", str(tmp_path))
    assert result == ""


def test_build_memory_prompt_truncates(tmp_path):
    for i in range(100):
        append_to_section("agent", str(tmp_path), "Task Log", f"Long entry {'x' * 200} number {i}")
    result = build_memory_prompt("agent", str(tmp_path), max_chars=500)
    assert len(result) <= 500


def test_load_shared_knowledge(tmp_path):
    shared_file = tmp_path / "_shared.md"
    shared_file.write_text("# Shared\n\n- API at port 8000\n")
    result = load_shared_knowledge(str(tmp_path))
    assert "port 8000" in result


def test_load_shared_knowledge_missing(tmp_path):
    result = load_shared_knowledge(str(tmp_path))
    assert result == ""


def test_generate_repo_map(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    result = generate_repo_map(str(tmp_path))
    assert "main.py" in result
    assert ".git" not in result


def test_log_and_get_decisions(tmp_path):
    log_decision(str(tmp_path), "cto", "Use FastAPI", "mature framework")
    log_decision(str(tmp_path), "ceo", "Target SMB first")
    result = get_recent_decisions(str(tmp_path))
    assert "FastAPI" in result
    assert "Target SMB" in result
    assert "cto" in result


def test_get_recent_decisions_limit(tmp_path):
    for i in range(15):
        log_decision(str(tmp_path), "agent", f"Decision {i}")
    result = get_recent_decisions(str(tmp_path), limit=5)
    lines = [l for l in result.strip().split("\n") if l.strip()]
    assert len(lines) == 5
    assert "Decision 14" in lines[-1]
