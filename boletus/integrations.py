"""Built-in integration catalog — hybrid: Claude.ai MCP + CLI + local MCP.

Three integration tiers:
1. Claude.ai MCP tools (Figma, Canva, Gamma, Notion, etc.) — zero setup, added to --allowedTools
2. CLI tools (GitHub via gh, AWS via aws CLI, etc.) — instructions in system prompt
3. Local MCP servers (PostgreSQL, etc.) — spawned as child processes via --mcp-config

During setup, the wizard collects credentials in Slack DMs and saves them to .env.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Each integration defines:
# - name: Human-friendly display name
# - description: What it does (shown in wizard checkbox)
# - env_vars: Required environment variables (collected during setup)
# - setup_message: Slack-formatted instructions for credential collection
# - agent_instructions: CLI fallback instructions injected into system prompt
# - auto_roles: Agent roles that get this integration by default
# - keywords: Used by wizard to auto-suggest from business description
# - claude_ai_tools (optional): Wildcard patterns for Claude.ai MCP tools (zero-setup)
# - mcp (optional): Local MCP server config — spawned as child process via npx

CATALOG = {
    # --- Code & Dev Tools ---
    "github": {
        "name": "GitHub",
        "description": "Repos, PRs, issues, code review, Actions",
        "env_vars": ["GITHUB_TOKEN"],
        "setup_message": (
            "*Connect GitHub*\n\n"
            "1. Go to <https://github.com/settings/tokens?type=beta|github.com/settings/tokens>\n"
            "2. Click *Generate new token*\n"
            "3. Give it a name (e.g. `boletus`), select scopes: `repo`, `workflow`\n"
            "4. Copy the token and *paste it here*"
        ),
        "agent_instructions": (
            "You have GitHub access via `gh` CLI and git.\n"
            "Use `gh repo create`, `gh pr create`, `gh issue create`, `gh issue list` etc.\n"
            "IMPORTANT: Always create PRIVATE repos (`gh repo create --private`). Never make repos public unless the owner explicitly asks.\n"
            "Use `git clone/commit/push` for code. GITHUB_TOKEN is set in your environment.\n"
            "Always use feature branches, never push directly to main."
        ),
        "auto_roles": ["manager"],
        "keywords": ["github", "git", "repository", "pull request", "issues", "code review", "repo"],
    },
    "linear": {
        "name": "Linear",
        "description": "Issues, projects, sprints, team tracking",
        "env_vars": ["LINEAR_API_KEY"],
        "setup_message": (
            "*Connect Linear*\n\n"
            "1. Go to Linear → *Settings* → *API*\n"
            "2. Create a *Personal API key*\n"
            "3. *Paste it here*"
        ),
        "agent_instructions": (
            "You have Linear access via the GraphQL API.\n"
            "Use curl: `curl -X POST https://api.linear.app/graphql "
            "-H 'Authorization: $LINEAR_API_KEY' -H 'Content-Type: application/json' "
            "-d '{\"query\": \"{ issues { nodes { title state { name } } } }\"}'`"
        ),
        "auto_roles": [],
        "keywords": ["linear", "issues", "project management", "tickets", "sprints"],
    },
    "sentry": {
        "name": "Sentry",
        "description": "Error tracking, performance monitoring",
        "env_vars": ["SENTRY_AUTH_TOKEN", "SENTRY_ORG"],
        "setup_message": (
            "*Connect Sentry*\n\n"
            "1. Go to <https://sentry.io/settings/account/api/auth-tokens/|Sentry Auth Tokens>\n"
            "2. Create a token with `project:read`, `event:read` scopes\n"
            "3. *Paste the token here*\n"
            "4. I'll ask for your org slug next"
        ),
        "agent_instructions": (
            "You have Sentry access. Use the REST API:\n"
            "`curl https://sentry.io/api/0/projects/ -H 'Authorization: Bearer $SENTRY_AUTH_TOKEN'`"
        ),
        "auto_roles": [],
        "keywords": ["sentry", "error tracking", "bugs", "monitoring", "crashes"],
    },
    "vercel": {
        "name": "Vercel",
        "description": "Deploy, manage projects, check deployments",
        "env_vars": ["VERCEL_TOKEN"],
        "setup_message": (
            "*Connect Vercel*\n\n"
            "1. Go to <https://vercel.com/account/tokens|vercel.com/account/tokens>\n"
            "2. Create a new token\n"
            "3. *Paste it here*"
        ),
        "agent_instructions": (
            "You have Vercel access. Use the `vercel` CLI or REST API:\n"
            "`curl https://api.vercel.com/v9/projects -H 'Authorization: Bearer $VERCEL_TOKEN'`"
        ),
        "auto_roles": ["manager"],
        "keywords": ["vercel", "deploy", "hosting", "serverless", "next.js"],
    },

    # --- Communication ---
    "gmail": {
        "name": "Gmail",
        "description": "Send and read emails, draft outreach",
        "env_vars": [],
        "setup_message": (
            "*Connect Gmail*\n\n"
            "Gmail works automatically via Claude.ai — no credentials needed!\n"
            "Your agents will be able to read and send emails.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have email access via SMTP. Use Python to send:\n"
            "```python\nimport smtplib, os\nfrom email.mime.text import MIMEText\n"
            "msg = MIMEText('body')\nmsg['Subject'] = 'subject'\n"
            "msg['From'] = os.environ['GMAIL_ADDRESS']\nmsg['To'] = 'to@example.com'\n"
            "with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:\n"
            "    s.login(os.environ['GMAIL_ADDRESS'], os.environ['GMAIL_APP_PASSWORD'])\n"
            "    s.send_message(msg)\n```"
        ),
        "claude_ai_tools": ["mcp__claude_ai_Gmail__*"],
        "auto_roles": ["leader"],
        "keywords": ["email", "outreach", "mail", "cold email", "inbox", "newsletter"],
    },
    "slack-extended": {
        "name": "Slack (extended)",
        "description": "Search messages, advanced channel management",
        "env_vars": [],
        "setup_message": (
            "*Slack extended access*\n\n"
            "This uses the same bot token — no extra setup needed!\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": "You have extended Slack access via the Slack API.",
        "auto_roles": [],
        "keywords": ["slack search", "search messages"],
    },

    # --- Google Workspace ---
    "google-calendar": {
        "name": "Google Calendar",
        "description": "Schedule meetings, check availability",
        "env_vars": [],
        "setup_message": (
            "*Connect Google Calendar*\n\n"
            "Google Calendar works automatically via Claude.ai — no credentials needed!\n"
            "Your agents will be able to check availability and create events.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have Google Calendar access via the API. Use curl or Python google-auth."
        ),
        "claude_ai_tools": ["mcp__claude_ai_Google_Calendar__*"],
        "auto_roles": ["leader"],
        "keywords": ["calendar", "meeting", "schedule", "booking", "availability"],
    },
    "google-drive": {
        "name": "Google Drive",
        "description": "Read, search, manage files and docs",
        "env_vars": [],
        "setup_message": (
            "*Connect Google Drive*\n\n"
            "Google Drive works automatically via Claude.ai — no credentials needed!\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have Google Drive access. Note: no direct API tools available.\n"
            "Use WebFetch to interact with shared Google Docs/Sheets links, or create "
            "local files and document them for the team."
        ),
        "auto_roles": [],
        "keywords": ["drive", "google drive", "files", "documents", "sheets", "spreadsheet"],
    },

    # --- Knowledge & Docs ---
    "notion": {
        "name": "Notion",
        "description": "Pages, databases, wiki, knowledge base",
        "env_vars": [],
        "setup_message": (
            "*Connect Notion*\n\n"
            "Notion works automatically via Claude.ai — no credentials needed!\n"
            "Your agents will be able to read and create Notion pages.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have Notion access via built-in MCP tools.\n"
            "IMPORTANT — Notion organization rules:\n"
            "1. FIRST use notion-search to find existing pages in the workspace\n"
            "2. Look for a top-level page with the company/project name\n"
            "3. If it doesn't exist, create one with notion-create-pages\n"
            "4. ALL your content goes UNDER that page as sub-pages\n"
            "5. Never create standalone private pages — always nest under the project page\n"
            "6. Use clear titles: 'Architecture Doc', 'Sprint 1 Plan', 'Competitor Research'\n"
            "Available tools: notion-search, notion-create-pages, notion-update-page, "
            "notion-fetch, notion-create-database, notion-create-comment"
        ),
        "claude_ai_tools": ["mcp__claude_ai_Notion__*"],
        "auto_roles": ["leader", "manager"],
        "keywords": ["notion", "wiki", "documentation", "knowledge base", "notes"],
    },
    "confluence": {
        "name": "Confluence",
        "description": "Read and write Confluence pages",
        "env_vars": ["CONFLUENCE_URL", "CONFLUENCE_TOKEN"],
        "setup_message": (
            "*Connect Confluence*\n\n"
            "1. Go to <https://id.atlassian.com/manage-profile/security/api-tokens|Atlassian API Tokens>\n"
            "2. Create a new token\n"
            "3. *Paste the token here*\n"
            "4. I'll ask for your Confluence URL next"
        ),
        "agent_instructions": (
            "You have Confluence access. Use the REST API:\n"
            "`curl $CONFLUENCE_URL/wiki/rest/api/content -H 'Authorization: Bearer $CONFLUENCE_TOKEN'`"
        ),
        "auto_roles": [],
        "keywords": ["confluence", "atlassian", "wiki", "documentation"],
    },

    # --- CRM & Sales ---
    "hubspot": {
        "name": "HubSpot",
        "description": "Contacts, deals, CRM pipeline",
        "env_vars": ["HUBSPOT_ACCESS_TOKEN"],
        "setup_message": (
            "*Connect HubSpot*\n\n"
            "1. Go to HubSpot → *Settings* → *Integrations* → *Private Apps*\n"
            "2. Create a new private app with CRM scopes\n"
            "3. Copy the access token and *paste it here*"
        ),
        "agent_instructions": (
            "You have HubSpot CRM access. Use the REST API:\n"
            "`curl https://api.hubapi.com/crm/v3/objects/contacts "
            "-H 'Authorization: Bearer $HUBSPOT_ACCESS_TOKEN'`"
        ),
        "auto_roles": [],
        "keywords": ["hubspot", "crm", "contacts", "deals", "sales", "pipeline"],
    },

    # --- Design ---
    "figma": {
        "name": "Figma",
        "description": "Read designs, export assets, inspect components",
        "env_vars": [],
        "setup_message": (
            "*Figma*\n\n"
            "No credentials needed — Figma works through Claude's built-in tools.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have Figma access via built-in tools.\n"
            "Use get_design_context, get_screenshot, get_metadata for reading designs.\n"
            "Use generate_diagram for creating FigJam diagrams."
        ),
        "claude_ai_tools": ["mcp__claude_ai_Figma__*"],
        "auto_roles": ["manager"],
        "keywords": ["figma", "design", "ui design", "mockup", "prototype", "wireframe"],
    },
    "canva": {
        "name": "Canva",
        "description": "Create designs, presentations, social media graphics",
        "env_vars": [],
        "setup_message": (
            "*Canva*\n\n"
            "No credentials needed — Canva works through Claude's built-in tools.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have Canva access via built-in tools.\n"
            "Use generate-design to create new designs, export-design to export them."
        ),
        "claude_ai_tools": ["mcp__claude_ai_Canva__*"],
        "auto_roles": ["manager"],
        "keywords": ["canva", "design", "presentation", "graphics", "social media", "logo"],
    },

    # --- Databases ---
    "postgres": {
        "name": "PostgreSQL",
        "description": "Query and manage SQL databases",
        "env_vars": ["DATABASE_URL"],
        "setup_message": (
            "*Connect PostgreSQL*\n\n"
            "Paste your connection string:\n"
            "`postgresql://user:password@host:5432/dbname`"
        ),
        "agent_instructions": (
            "You have PostgreSQL access. Use `psql` or Python:\n"
            "`psql $DATABASE_URL -c 'SELECT ...'`"
        ),
        "auto_roles": [],
        "keywords": ["postgres", "database", "sql", "db", "query", "postgresql"],
        "mcp": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "${DATABASE_URL}"]},
    },
    "supabase": {
        "name": "Supabase",
        "description": "Database, auth, storage, edge functions",
        "env_vars": ["SUPABASE_URL", "SUPABASE_KEY"],
        "setup_message": (
            "*Connect Supabase*\n\n"
            "1. Go to your Supabase project → *Settings* → *API*\n"
            "2. Copy the *Project URL* and *paste it here*\n"
            "3. I'll ask for the `anon` key next"
        ),
        "agent_instructions": (
            "You have Supabase access. Use curl or the supabase CLI:\n"
            "`curl '$SUPABASE_URL/rest/v1/TABLE' "
            "-H 'apikey: $SUPABASE_KEY' -H 'Authorization: Bearer $SUPABASE_KEY'`"
        ),
        "auto_roles": [],
        "keywords": ["supabase", "database", "backend", "auth", "storage"],
    },
    "mongodb": {
        "name": "MongoDB",
        "description": "Query and manage MongoDB databases",
        "env_vars": ["MONGODB_URI"],
        "setup_message": (
            "*Connect MongoDB*\n\n"
            "Paste your connection string:\n"
            "`mongodb+srv://user:password@cluster.mongodb.net/dbname`"
        ),
        "agent_instructions": (
            "You have MongoDB access. Use `mongosh` or Python pymongo:\n"
            "`mongosh $MONGODB_URI --eval 'db.collection.find()'`"
        ),
        "auto_roles": [],
        "keywords": ["mongodb", "mongo", "nosql", "document database"],
    },

    # --- Analytics & Monitoring ---
    "posthog": {
        "name": "PostHog",
        "description": "Product analytics, feature flags, experiments",
        "env_vars": [],
        "setup_message": (
            "*Connect PostHog*\n\n"
            "PostHog works automatically via Claude.ai — no credentials needed!\n"
            "Your agents will be able to query analytics and manage feature flags.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have PostHog access. Use the API:\n"
            "`curl https://app.posthog.com/api/projects/$POSTHOG_PROJECT_ID/insights/ "
            "-H 'Authorization: Bearer $POSTHOG_API_KEY'`"
        ),
        "claude_ai_tools": ["mcp__claude_ai_PostHog__*"],
        "auto_roles": [],
        "keywords": ["posthog", "analytics", "feature flags", "experiments", "product analytics"],
    },

    # --- Cloud & Infrastructure ---
    "cloudflare": {
        "name": "Cloudflare",
        "description": "DNS, Workers, R2, D1, KV",
        "env_vars": [],
        "setup_message": (
            "*Connect Cloudflare*\n\n"
            "Cloudflare works automatically via Claude.ai — no credentials needed!\n"
            "Your agents will be able to manage Workers, R2, D1, and KV.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have Cloudflare access. Use `wrangler` CLI or the API:\n"
            "`curl https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts "
            "-H 'Authorization: Bearer $CLOUDFLARE_API_TOKEN'`"
        ),
        "claude_ai_tools": ["mcp__claude_ai_Cloudflare_Developer_Platform__*"],
        "auto_roles": [],
        "keywords": ["cloudflare", "dns", "workers", "cdn", "r2", "d1", "edge"],
    },
    "aws": {
        "name": "AWS",
        "description": "S3, Lambda, EC2, and other AWS services",
        "env_vars": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"],
        "setup_message": (
            "*Connect AWS*\n\n"
            "1. Go to <https://console.aws.amazon.com/iam/home#/security_credentials|AWS Security Credentials>\n"
            "2. Create a new Access Key\n"
            "3. *Paste the Access Key ID here*\n"
            "4. I'll ask for the Secret Key and region next"
        ),
        "agent_instructions": (
            "You have AWS access via the `aws` CLI.\n"
            "Use `aws s3 ls`, `aws lambda list-functions`, etc.\n"
            "AWS credentials are set in your environment."
        ),
        "auto_roles": [],
        "keywords": ["aws", "amazon", "s3", "lambda", "ec2", "cloud"],
    },

    # --- Payments ---
    "stripe": {
        "name": "Stripe",
        "description": "Payments, subscriptions, invoices",
        "env_vars": ["STRIPE_SECRET_KEY"],
        "setup_message": (
            "*Connect Stripe*\n\n"
            "1. Go to <https://dashboard.stripe.com/apikeys|Stripe API Keys>\n"
            "2. Copy your *Secret key* (starts with `sk_`)\n"
            "3. *Paste it here*"
        ),
        "agent_instructions": (
            "You have Stripe access. Use the `stripe` CLI or curl:\n"
            "`curl https://api.stripe.com/v1/customers "
            "-u $STRIPE_SECRET_KEY:`"
        ),
        "auto_roles": [],
        "keywords": ["stripe", "payments", "billing", "subscriptions", "invoices"],
    },

    # --- Project Management ---
    "jira": {
        "name": "Jira",
        "description": "Issues, boards, sprints, epics",
        "env_vars": ["JIRA_URL", "JIRA_TOKEN", "JIRA_EMAIL"],
        "setup_message": (
            "*Connect Jira*\n\n"
            "1. Go to <https://id.atlassian.com/manage-profile/security/api-tokens|Atlassian API Tokens>\n"
            "2. Create a new token\n"
            "3. *Paste the token here*\n"
            "4. I'll ask for your Jira URL and email next"
        ),
        "agent_instructions": (
            "You have Jira access. Use the REST API:\n"
            "`curl '$JIRA_URL/rest/api/3/search' "
            "-H 'Authorization: Basic $(echo -n $JIRA_EMAIL:$JIRA_TOKEN | base64)'`"
        ),
        "auto_roles": [],
        "keywords": ["jira", "atlassian", "issues", "boards", "sprints", "epics"],
    },
    "airtable": {
        "name": "Airtable",
        "description": "Databases, spreadsheets, automations",
        "env_vars": ["AIRTABLE_TOKEN"],
        "setup_message": (
            "*Connect Airtable*\n\n"
            "1. Go to <https://airtable.com/create/tokens|Airtable Tokens>\n"
            "2. Create a personal access token\n"
            "3. *Paste it here*"
        ),
        "agent_instructions": (
            "You have Airtable access. Use the API:\n"
            "`curl 'https://api.airtable.com/v0/BASE_ID/TABLE' "
            "-H 'Authorization: Bearer $AIRTABLE_TOKEN'`"
        ),
        "auto_roles": [],
        "keywords": ["airtable", "spreadsheet", "database", "tables"],
    },

    # --- Content & Presentations ---
    "gamma": {
        "name": "Gamma",
        "description": "Create AI presentations, docs, websites",
        "env_vars": [],
        "setup_message": (
            "*Gamma*\n\n"
            "No credentials needed — Gamma works through Claude's built-in tools.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You can create presentations, documents, and websites using Gamma.\n"
            "Use the generate tool to create content."
        ),
        "claude_ai_tools": ["mcp__claude_ai_Gamma__*"],
        "auto_roles": ["manager"],
        "keywords": ["gamma", "presentation", "slides", "pitch deck", "deck"],
    },
    "miro": {
        "name": "Miro",
        "description": "Whiteboards, diagrams, brainstorming",
        "env_vars": [],
        "setup_message": (
            "*Miro*\n\n"
            "No credentials needed — Miro works through Claude's built-in tools.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": (
            "You have Miro access via built-in tools.\n"
            "Use diagram_create for creating diagrams, doc_create for documents."
        ),
        "claude_ai_tools": ["mcp__claude_ai_Miro__*"],
        "auto_roles": [],
        "keywords": ["miro", "whiteboard", "diagram", "brainstorm", "flowchart"],
    },
    "granola": {
        "name": "Granola",
        "description": "Meeting transcripts and notes",
        "env_vars": [],
        "setup_message": (
            "*Granola*\n\n"
            "No credentials needed — Granola works through Claude's built-in tools.\n"
            "Type `skip` to continue."
        ),
        "agent_instructions": "You can access meeting transcripts via Granola.",
        "claude_ai_tools": ["mcp__claude_ai_Granola__*"],
        "auto_roles": [],
        "keywords": ["granola", "meeting", "transcript", "meeting notes"],
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
        # Resolve ${ENV_VAR} placeholders in args
        resolved_args = []
        skip_server = False
        for arg in mcp["args"]:
            if arg.startswith("${") and arg.endswith("}"):
                var_name = arg[2:-1]
                val = os.environ.get(var_name, "")
                if val:
                    resolved_args.append(val)
                else:
                    logger.warning(
                        f"Skipping MCP server '{name}': env var {var_name} is not set"
                    )
                    skip_server = True
                    break
            else:
                resolved_args.append(arg)
        if skip_server:
            continue
        server = {
            "command": mcp["command"],
            "args": resolved_args,
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


def get_claude_ai_tools_for_integrations(integration_names: list[str]) -> list[str]:
    """Return Claude.ai MCP tool wildcard patterns for the given integrations.

    These patterns are appended to --allowedTools so agents can use
    Claude's built-in MCP tools (Figma, Canva, Gamma, Notion, etc.)
    without any local MCP server infrastructure.
    """
    patterns = []
    for name in integration_names:
        integration = CATALOG.get(name)
        if not integration:
            continue
        for pattern in integration.get("claude_ai_tools", []):
            if pattern not in patterns:
                patterns.append(pattern)
    return patterns


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
        f.write("# Boletus — auto-generated credentials\n")
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
