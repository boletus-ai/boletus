"""Built-in integration catalog — CLI-first, MCP optional.

Agents use CLI tools (gh, git, curl) by default. MCP servers are optional
for users who want structured tool access. Credentials are collected during
the setup wizard and saved to .env.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Each integration defines:
# - name: Human-friendly display name
# - description: What it does (shown in wizard)
# - env_vars: Required environment variables
# - setup_message: Slack-formatted instructions shown during credential collection
# - agent_instructions: Injected into agent system prompt so it knows how to use the tool
# - auto_roles: Agent roles that get this integration by default
# - keywords: Used by the wizard to match user descriptions to integrations
# - mcp (optional): MCP server config for advanced users

CATALOG = {
    "github": {
        "name": "GitHub",
        "description": "Create repos, PRs, issues, review code",
        "env_vars": ["GITHUB_TOKEN"],
        "setup_message": (
            "*Connect GitHub*\n\n"
            "1. Go to <https://github.com/settings/tokens?type=beta|github.com/settings/tokens>\n"
            "2. Click *Generate new token*\n"
            "3. Give it a name (e.g. `crewmatic`)\n"
            "4. Select scopes: `repo`, `workflow`\n"
            "5. Copy the token and *paste it here*"
        ),
        "agent_instructions": (
            "You have GitHub access via the `gh` CLI and git.\n"
            "- Use `gh repo create`, `gh pr create`, `gh issue create` etc.\n"
            "- Use `git clone/commit/push` for code work.\n"
            "- GITHUB_TOKEN is set in your environment.\n"
            "- Always create feature branches, never push directly to main."
        ),
        "auto_roles": [],
        "keywords": ["github", "git", "repository", "pull request", "issues", "code review", "repo"],
        "mcp": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
    },
    "gmail": {
        "name": "Gmail",
        "description": "Send and read emails, draft outreach",
        "env_vars": ["GMAIL_APP_PASSWORD", "GMAIL_ADDRESS"],
        "setup_message": (
            "*Connect Gmail*\n\n"
            "1. Go to <https://myaccount.google.com/apppasswords|Google App Passwords>\n"
            "   (requires 2FA enabled on your Google account)\n"
            "2. Create a new app password for `crewmatic`\n"
            "3. *Paste the 16-character password here*\n"
            "4. I'll ask for your Gmail address next"
        ),
        "agent_instructions": (
            "You have email access. To send emails, use Python or curl:\n"
            "```\npython3 -c \"\nimport smtplib\nfrom email.mime.text import MIMEText\nimport os\n"
            "msg = MIMEText('body')\nmsg['Subject'] = 'subject'\nmsg['From'] = os.environ['GMAIL_ADDRESS']\n"
            "msg['To'] = 'recipient@example.com'\n"
            "with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:\n"
            "    s.login(os.environ['GMAIL_ADDRESS'], os.environ['GMAIL_APP_PASSWORD'])\n"
            "    s.send_message(msg)\n\"\n```\n"
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD are set in your environment."
        ),
        "auto_roles": ["leader"],
        "keywords": ["email", "outreach", "mail", "cold email", "inbox", "send email", "newsletter"],
    },
    "notion": {
        "name": "Notion",
        "description": "Read and write Notion pages and databases",
        "env_vars": ["NOTION_TOKEN"],
        "setup_message": (
            "*Connect Notion*\n\n"
            "1. Go to <https://www.notion.so/my-integrations|notion.so/my-integrations>\n"
            "2. Click *New integration*\n"
            "3. Give it a name, select your workspace\n"
            "4. Copy the *Internal Integration Secret*\n"
            "5. *Paste it here*\n\n"
            "Then share the pages you want accessible with this integration."
        ),
        "agent_instructions": (
            "You have Notion access via the API. Use curl with your NOTION_TOKEN:\n"
            "- Search: `curl -X POST 'https://api.notion.com/v1/search' -H 'Authorization: Bearer $NOTION_TOKEN' -H 'Notion-Version: 2022-06-28'`\n"
            "- NOTION_TOKEN is set in your environment."
        ),
        "auto_roles": [],
        "keywords": ["notion", "wiki", "documentation", "knowledge base", "notes"],
        "mcp": {"command": "npx", "args": ["-y", "@anthropic/mcp-server-notion"]},
    },
    "linear": {
        "name": "Linear",
        "description": "Create and manage issues, track projects",
        "env_vars": ["LINEAR_API_KEY"],
        "setup_message": (
            "*Connect Linear*\n\n"
            "1. Go to Linear → *Settings* → *API*\n"
            "2. Create a new *Personal API key*\n"
            "3. *Paste it here*"
        ),
        "agent_instructions": (
            "You have Linear access. Use the GraphQL API via curl:\n"
            "- `curl -X POST https://api.linear.app/graphql -H 'Authorization: $LINEAR_API_KEY'`\n"
            "- LINEAR_API_KEY is set in your environment."
        ),
        "auto_roles": [],
        "keywords": ["linear", "issues", "project management", "tickets", "sprints"],
    },
    "postgres": {
        "name": "PostgreSQL",
        "description": "Query and manage databases",
        "env_vars": ["DATABASE_URL"],
        "setup_message": (
            "*Connect PostgreSQL*\n\n"
            "Paste your connection string:\n"
            "`postgresql://user:password@host:5432/dbname`"
        ),
        "agent_instructions": (
            "You have PostgreSQL access. Use `psql` or Python:\n"
            "- `psql $DATABASE_URL -c 'SELECT ...'`\n"
            "- DATABASE_URL is set in your environment."
        ),
        "auto_roles": [],
        "keywords": ["postgres", "database", "sql", "db", "query", "postgresql"],
        "mcp": {"command": "npx", "args": ["-y", "@anthropic/mcp-server-postgres"]},
    },
    "hubspot": {
        "name": "HubSpot",
        "description": "Manage contacts, deals, CRM",
        "env_vars": ["HUBSPOT_ACCESS_TOKEN"],
        "setup_message": (
            "*Connect HubSpot*\n\n"
            "1. Go to HubSpot → *Settings* → *Integrations* → *Private Apps*\n"
            "2. Create a new private app\n"
            "3. Select scopes: `crm.objects.contacts`, `crm.objects.deals`\n"
            "4. Copy the access token and *paste it here*"
        ),
        "agent_instructions": (
            "You have HubSpot CRM access. Use the REST API via curl:\n"
            "- `curl https://api.hubapi.com/crm/v3/objects/contacts -H 'Authorization: Bearer $HUBSPOT_ACCESS_TOKEN'`\n"
            "- HUBSPOT_ACCESS_TOKEN is set in your environment."
        ),
        "auto_roles": [],
        "keywords": ["hubspot", "crm", "contacts", "deals", "sales", "pipeline"],
    },
}


def get_integration(name: str) -> dict | None:
    """Look up an integration by name. Returns None if not found."""
    return CATALOG.get(name)


def list_integrations() -> list[dict]:
    """Return all available integrations as a list with keys included."""
    result = []
    for key, integration in CATALOG.items():
        result.append({"key": key, **integration})
    return result


def build_mcp_config_for_integrations(integration_names: list[str]) -> dict:
    """Build a Claude CLI MCP config dict for integrations that have MCP support.

    Only includes integrations that have a "mcp" field in the catalog.
    Returns dict in the format: {"mcpServers": {"name": {"command": ..., "args": ..., "env": ...}}}
    """
    servers = {}
    for name in integration_names:
        integration = CATALOG.get(name)
        if not integration:
            logger.warning(f"Unknown integration: {name}")
            continue
        mcp = integration.get("mcp")
        if not mcp:
            continue  # CLI-only integration, no MCP server
        server = {
            "command": mcp["command"],
            "args": mcp["args"],
        }
        env = {}
        for var in integration.get("env_vars", []):
            val = os.environ.get(var, "")
            if val:
                env[var] = val
        if env:
            server["env"] = env
        servers[name] = server
    return {"mcpServers": servers}


def get_agent_integration_instructions(integration_names: list[str]) -> str:
    """Build system prompt instructions for an agent's integrations.

    Returns a string to append to the agent's system prompt, telling it
    how to use each integration via CLI tools.
    """
    parts = []
    for name in integration_names:
        integration = CATALOG.get(name)
        if not integration:
            continue
        instructions = integration.get("agent_instructions", "")
        if instructions:
            parts.append(f"### {integration['name']}\n{instructions}")
    if not parts:
        return ""
    return "\n\nINTEGRATIONS AVAILABLE TO YOU:\n" + "\n\n".join(parts)


def save_credentials_to_env(config_dir: str, credentials: dict[str, str]) -> str:
    """Append integration credentials to .env file.

    Args:
        config_dir: Directory containing crew.yaml
        credentials: Dict of ENV_VAR_NAME -> value

    Returns:
        Path to .env file
    """
    env_path = os.path.join(config_dir, ".env")

    # Read existing .env to avoid duplicates
    existing = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    existing[key.strip()] = val.strip()

    # Merge — new values overwrite old
    existing.update(credentials)

    # Write back
    with open(env_path, "w") as f:
        f.write("# Crewmatic — auto-generated credentials\n")
        for key, val in sorted(existing.items()):
            # Don't quote if already quoted
            if val and not (val.startswith('"') or val.startswith("'")):
                val = f'"{val}"'
            f.write(f"{key}={val}\n")

    logger.info(f"Saved {len(credentials)} credentials to {env_path}")
    return env_path


def resolve_integrations_for_agent(
    agent_role: str,
    agent_integrations: list[str] | None,
    global_integrations: list[str],
) -> list[str]:
    """Determine which integrations an agent should have.

    Priority:
    1. If agent has explicit integrations: list, use that
    2. Otherwise, auto-assign based on role + what's globally enabled
    """
    if agent_integrations is not None:
        return agent_integrations

    # Auto-assign: intersection of global integrations and role defaults
    result = []
    for name in global_integrations:
        integration = CATALOG.get(name)
        if not integration:
            continue
        if agent_role in integration.get("auto_roles", []):
            result.append(name)
    return result


def check_integration_credentials(integration_names: list[str]) -> list[tuple[str, str, bool]]:
    """Check which integrations have their required env vars set.

    Returns list of (integration_name, env_var, is_set) tuples.
    """
    import os

    results = []
    for name in integration_names:
        integration = CATALOG.get(name)
        if not integration:
            continue
        for var in integration.get("env_vars", []):
            is_set = bool(os.environ.get(var, ""))
            results.append((name, var, is_set))
    return results


def match_integrations_from_description(description: str) -> list[str]:
    """Match integrations based on keywords in a business description.

    Used by the setup wizard to suggest integrations.
    """
    description_lower = description.lower()
    matches = []
    for key, integration in CATALOG.items():
        for keyword in integration.get("keywords", []):
            if keyword in description_lower:
                if key not in matches:
                    matches.append(key)
                break
    return matches
