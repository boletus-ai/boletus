"""Tests for crewmatic.integrations — integration catalog and helpers."""

import os
import tempfile

from crewmatic.integrations import (
    get_integration, list_integrations, CATALOG,
    resolve_integrations_for_agent, match_integrations_from_description,
    build_mcp_config_for_integrations, check_integration_credentials,
    get_agent_integration_instructions, get_claude_ai_tools_for_integrations,
    save_credentials_to_env,
)


def test_catalog_not_empty():
    assert len(CATALOG) > 0


def test_get_known_integration():
    github = get_integration("github")
    assert github is not None
    assert github["name"] == "GitHub"
    assert "env_vars" in github
    assert "agent_instructions" in github


def test_get_unknown_integration():
    assert get_integration("nonexistent") is None


def test_list_integrations_has_keys():
    items = list_integrations()
    assert len(items) > 0
    assert all("key" in item for item in items)
    assert all("name" in item for item in items)


def test_list_integrations_count_matches_catalog():
    items = list_integrations()
    assert len(items) == len(CATALOG)


def test_resolve_explicit_override():
    result = resolve_integrations_for_agent("worker", ["github"], ["gmail", "github"])
    assert result == ["github"]


def test_resolve_auto_assignment():
    result = resolve_integrations_for_agent("leader", None, ["gmail", "github"])
    assert "gmail" in result


def test_resolve_auto_assignment_no_match():
    result = resolve_integrations_for_agent("worker", None, ["gmail", "github"])
    assert result == []


def test_resolve_no_integrations():
    result = resolve_integrations_for_agent("worker", None, [])
    assert result == []


def test_resolve_explicit_empty_list():
    result = resolve_integrations_for_agent("leader", [], ["gmail"])
    assert result == []


def test_match_from_description_email():
    matches = match_integrations_from_description("I need to send cold emails to prospects")
    assert "gmail" in matches


def test_match_from_description_github():
    matches = match_integrations_from_description("We have a GitHub repository")
    assert "github" in matches


def test_match_from_description_multiple():
    matches = match_integrations_from_description("We use GitHub for code and Notion for docs")
    assert "github" in matches
    assert "notion" in matches


def test_match_no_keywords():
    matches = match_integrations_from_description("I sell handmade jewelry at craft fairs")
    assert isinstance(matches, list)


def test_build_mcp_config_with_mcp():
    """Integrations with mcp field get included."""
    config = build_mcp_config_for_integrations(["postgres"])
    assert "mcpServers" in config
    assert "postgres" in config["mcpServers"]


def test_build_mcp_config_without_mcp():
    """Integrations without mcp field are skipped (CLI-only or Claude.ai-only)."""
    config = build_mcp_config_for_integrations(["github"])
    assert config["mcpServers"] == {}  # github uses CLI, no local MCP


def test_claude_ai_tools_for_figma():
    """Figma should return Claude.ai MCP tool patterns."""
    patterns = get_claude_ai_tools_for_integrations(["figma"])
    assert len(patterns) == 1
    assert "mcp__claude_ai_Figma__*" in patterns


def test_claude_ai_tools_for_multiple():
    """Multiple integrations return combined patterns."""
    patterns = get_claude_ai_tools_for_integrations(["figma", "canva", "gamma"])
    assert "mcp__claude_ai_Figma__*" in patterns
    assert "mcp__claude_ai_Canva__*" in patterns
    assert "mcp__claude_ai_Gamma__*" in patterns


def test_claude_ai_tools_no_duplicates():
    """Same integration listed twice shouldn't duplicate patterns."""
    patterns = get_claude_ai_tools_for_integrations(["figma", "figma"])
    assert len(patterns) == 1


def test_claude_ai_tools_cli_only():
    """CLI-only integrations return no Claude.ai tool patterns."""
    patterns = get_claude_ai_tools_for_integrations(["github", "aws"])
    assert patterns == []


def test_claude_ai_tools_empty():
    assert get_claude_ai_tools_for_integrations([]) == []


def test_claude_ai_tools_unknown():
    assert get_claude_ai_tools_for_integrations(["nonexistent"]) == []


def test_build_mcp_config_unknown():
    config = build_mcp_config_for_integrations(["nonexistent"])
    assert config["mcpServers"] == {}


def test_build_mcp_config_empty():
    config = build_mcp_config_for_integrations([])
    assert config["mcpServers"] == {}


def test_check_integration_credentials_unknown():
    results = check_integration_credentials(["nonexistent"])
    assert results == []


def test_check_integration_credentials_structure():
    results = check_integration_credentials(["github"])
    assert len(results) > 0
    name, var, is_set = results[0]
    assert name == "github"
    assert var == "GITHUB_TOKEN"
    assert isinstance(is_set, bool)


def test_agent_integration_instructions():
    instructions = get_agent_integration_instructions(["github"])
    assert "GitHub" in instructions
    assert "gh" in instructions  # mentions gh CLI


def test_agent_integration_instructions_empty():
    assert get_agent_integration_instructions([]) == ""


def test_agent_integration_instructions_unknown():
    assert get_agent_integration_instructions(["nonexistent"]) == ""


def test_save_credentials_to_env(tmp_path):
    env_path = save_credentials_to_env(str(tmp_path), {"GITHUB_TOKEN": "ghp_test123"})
    assert os.path.exists(env_path)
    content = open(env_path).read()
    assert "GITHUB_TOKEN" in content
    assert "ghp_test123" in content


def test_save_credentials_merges(tmp_path):
    save_credentials_to_env(str(tmp_path), {"KEY1": "val1"})
    save_credentials_to_env(str(tmp_path), {"KEY2": "val2"})
    content = open(os.path.join(str(tmp_path), ".env")).read()
    assert "KEY1" in content
    assert "KEY2" in content
