"""Tests for agent memory persistence — append_agent_memory."""

import os

from crewmatic.context import append_agent_memory, load_agent_memory


def test_append_creates_file(tmp_path):
    append_agent_memory("ceo", str(tmp_path), "First planning session")
    content = load_agent_memory("ceo", str(tmp_path))
    assert "First planning session" in content
    assert "## [" in content  # has timestamp header


def test_append_accumulates(tmp_path):
    append_agent_memory("cto", str(tmp_path), "Built API endpoint")
    append_agent_memory("cto", str(tmp_path), "Fixed auth bug")
    content = load_agent_memory("cto", str(tmp_path))
    assert "Built API endpoint" in content
    assert "Fixed auth bug" in content


def test_append_preserves_existing(tmp_path):
    # Agent wrote their own memory
    mem_file = tmp_path / "dev.md"
    mem_file.write_text("# Dev Memory\n\nI prefer functional style.\n")

    append_agent_memory("dev", str(tmp_path), "Completed task #5")
    content = mem_file.read_text()
    assert "I prefer functional style" in content
    assert "Completed task #5" in content


def test_append_creates_directory(tmp_path):
    deep = str(tmp_path / "nested" / "memory")
    append_agent_memory("cmo", deep, "Market research done")
    assert os.path.exists(os.path.join(deep, "cmo.md"))


def test_auto_trim_large_file(tmp_path):
    # Write >50KB of memory
    mem_file = tmp_path / "agent.md"
    big_content = ""
    for i in range(600):
        big_content += f"\n\n## [2026-01-01 {i:02d}:00]\nEntry number {i}. {'x' * 100}\n"
    mem_file.write_text(big_content)
    assert len(big_content) > 50_000

    # This append should trigger trimming
    append_agent_memory("agent", str(tmp_path), "New entry after trim")
    content = mem_file.read_text()
    assert len(content) < 50_000
    assert "New entry after trim" in content
    assert "auto-trimmed" in content
