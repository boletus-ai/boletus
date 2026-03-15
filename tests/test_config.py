"""Tests for config loading."""

import os
import tempfile

import pytest

from boletus.config import load_config, _interpolate_env


def test_interpolate_env():
    os.environ["TEST_CREWMATIC_VAR"] = "hello"
    assert _interpolate_env("token: ${TEST_CREWMATIC_VAR}") == "token: hello"
    del os.environ["TEST_CREWMATIC_VAR"]


def test_load_minimal_config(tmp_path):
    config_file = tmp_path / "crew.yaml"
    config_file.write_text("""
name: "Test Crew"
agents:
  lead:
    channel: "lead"
    role: "leader"
    system_prompt: "You are the lead."
  dev:
    channel: "dev"
    role: "worker"
    system_prompt: "You are a developer."
    reports_to: lead
""")
    config = load_config(str(config_file))
    assert config["name"] == "Test Crew"
    assert "lead" in config["agents"]
    assert "dev" in config["agents"]


def test_missing_agent_channel(tmp_path):
    config_file = tmp_path / "crew.yaml"
    config_file.write_text("""
name: "Bad Config"
agents:
  lead:
    role: "leader"
    system_prompt: "You are the lead."
""")
    with pytest.raises(ValueError, match="missing 'channel'"):
        load_config(str(config_file))


def test_invalid_delegation_target(tmp_path):
    config_file = tmp_path / "crew.yaml"
    config_file.write_text("""
name: "Bad Delegation"
agents:
  lead:
    channel: "lead"
    role: "leader"
    system_prompt: "Lead"
    delegates_to: [ghost]
""")
    with pytest.raises(ValueError, match="unknown agent 'ghost'"):
        load_config(str(config_file))
