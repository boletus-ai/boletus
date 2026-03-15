"""Tests for delegation parsing."""

from boletus.delegation import parse_delegations, parse_unknown_delegations, handle_delegations


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


def test_multiline_delegation():
    response = (
        "@dev: Build the payment API endpoint.\n"
        "  It should support Stripe webhooks\n"
        "  and return proper error codes.\n"
        "\n"
        "@designer: Create the landing page mockup"
    )
    result = parse_delegations(response, AGENT_NAMES)
    assert len(result) == 2
    assert "Stripe webhooks" in result[0][1]
    assert "error codes" in result[0][1]
    assert result[1][0] == "designer"


def test_multiline_stops_at_blank():
    response = (
        "@cto: Review the auth implementation\n"
        "  Check for security issues\n"
        "\n"
        "Some unrelated text here."
    )
    result = parse_delegations(response, AGENT_NAMES)
    assert len(result) == 1
    assert "security issues" in result[0][1]
    assert "unrelated" not in result[0][1]


def test_empty_agent_names():
    result = parse_delegations("@cto: do something important here", set())
    assert len(result) == 0


# --- Auto-hire / unknown delegation tests ---

def test_unknown_delegations_found():
    response = "@cto: implement auth\n@sales_rep: Build a list of target companies and start outreach"
    unknown = parse_unknown_delegations(response, AGENT_NAMES)
    assert len(unknown) == 1
    assert unknown[0][0] == "sales_rep"
    assert "target companies" in unknown[0][1]


def test_unknown_delegations_ignores_known():
    response = "@cto: do something really important here"
    unknown = parse_unknown_delegations(response, AGENT_NAMES)
    assert unknown == []


def test_unknown_delegations_skips_false_positives():
    response = "@here: look at this thing everyone should see"
    unknown = parse_unknown_delegations(response, AGENT_NAMES)
    assert unknown == []


def test_unknown_delegations_bold_pattern():
    response = "**content_writer**: Write 5 blog posts about AI recruiting trends"
    unknown = parse_unknown_delegations(response, AGENT_NAMES)
    assert len(unknown) == 1
    assert unknown[0][0] == "content_writer"


def test_handle_delegations_returns_unknown():
    tasks_added = []
    def mock_add(title, assigned_to=None, created_by=None, priority="medium", details=""):
        tasks_added.append((title, assigned_to))

    response = "@cto: build the API\n@data_analyst: analyze conversion funnel metrics"
    unknown = handle_delegations("ceo", response, AGENT_NAMES, mock_add)
    assert len(tasks_added) == 1  # only known agent
    assert tasks_added[0][1] == "cto"
    assert len(unknown) == 1
    assert unknown[0][0] == "data_analyst"
