"""Tests for delegation parsing."""

from crewmatic.delegation import parse_delegations


AGENT_NAMES = {"cto", "cmo", "dev", "designer"}


def test_at_pattern():
    response = "Let's get this done.\n@cto: implement auth module\n@cmo: research competitors"
    result = parse_delegations(response, AGENT_NAMES)
    assert len(result) == 2
    assert result[0] == ("cto", "implement auth module")
    assert result[1] == ("cmo", "research competitors")


def test_bold_pattern():
    response = "**CTO**: implement the new API endpoint for users"
    result = parse_delegations(response, AGENT_NAMES)
    assert len(result) == 1
    assert result[0][0] == "cto"


def test_slack_bold_pattern():
    response = "*CTO*: implement the new API endpoint for users"
    result = parse_delegations(response, AGENT_NAMES)
    assert len(result) == 1
    assert result[0][0] == "cto"


def test_skips_short_tasks():
    response = "@cto: fix"
    result = parse_delegations(response, AGENT_NAMES)
    assert len(result) == 0


def test_skips_unknown_agents():
    response = "@janitor: clean the codebase and remove dead code"
    result = parse_delegations(response, AGENT_NAMES)
    assert len(result) == 0
