# Crewmatic

**Your first AI company** — open-source framework that runs an autonomous AI team via Slack + Claude CLI.

Send a business plan to your CEO agent. It hires a team, delegates tasks, writes code, creates marketing — all autonomously.

## Quickstart

### Prerequisites

- Python 3.11+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- A Slack workspace ([create one free](https://slack.com/create))
- (Optional) Connect services in [claude.ai/settings](https://claude.ai/settings) → Integrations (Gmail, Notion, Figma, etc.) — your AI team will use these automatically

### 1. Install

```bash
git clone https://github.com/Majny/crewmatic.git
cd crewmatic
./install.sh
```

The script creates a virtual environment, installs dependencies, and walks you through connecting Slack:

```
=== Slack Setup ===

1. Go to https://api.slack.com/apps
2. Click 'Create New App' > 'From a manifest'
3. Select your workspace
4. Paste the contents of slack-app-manifest.json
5. Click 'Create'

Now get your tokens:

  App Token: Basic Information > App-Level Tokens > Generate
  Paste App Token (xapp-...): ****

  Bot Token: Install App > Install to Workspace > copy
  Paste Bot Token (xoxb-...): ****

  Your Slack User ID: click your profile > three dots > Copy member ID
  Paste Member ID (U...): U12345678

Saved tokens to .env
```

### 2. Start

```bash
source .venv/bin/activate
crewmatic setup
```

The wizard DMs you in Slack, asks about your business, generates `crew.yaml`, creates channels, and starts the team.

### 3. Send a business plan

Go to `#ceo` in Slack and tell the CEO what to build. The team starts working autonomously.

## How it works

```
You: "Build a SaaS for restaurant analytics"
        │
        ▼
   ┌─────────┐     ┌────────────┐     ┌──────────┐
   │   CEO   │────>│ Task Board │────>│ Workers  │
   │ (plans) │     │            │<────│(execute) │
   └─────────┘     └────────────┘     └──────────┘
        │                                    │
        ├── hires @cfo, @sales_rep...       │
        └──────── Slack channels ───────────┘
```

1. **CEO** receives your business plan, breaks it into tasks, delegates
2. **CTO** hires developers and testers as needed, manages code quality
3. **CMO** researches markets, creates content using Canva/Gamma/Figma
4. **Workers** claim tasks, execute via Claude CLI, report results
5. **Auto-hiring** — agents create new roles on the fly when workload demands it
6. **Self-correction** — workers escalate blockers, managers reassess strategy

### Dynamic team

Start with 3 agents (CEO, CTO, CMO). They hire more as needed:

```
CEO writes: @sales_rep: Build a list of 50 target companies and start outreach
→ system auto-creates sales_rep agent, Slack channel, starts working

CTO writes: @tester: Write integration tests for the payment API
→ system auto-creates tester agent, assigns first task
```

No manual configuration needed — the team grows organically.

## Slack commands

| Command | Description |
|---------|-------------|
| `tasks` | Show task board |
| `team` | Show current team structure |
| `costs` | Show cost tracker (per agent, per day) |
| `report` | Run progress report |
| `standup` | Run team standup |
| `my tasks` | Tasks for this channel's agent |
| `cancel #42 reason` | Cancel a task |
| `start <project>` | Activate a project |
| `stop` | Stop current project |
| `status` | Project status |
| `help` | List all commands |

## CLI commands

```bash
crewmatic setup         # Recommended — Slack wizard creates team + channels + starts bot
crewmatic run           # Start the bot (requires crew.yaml)
crewmatic run -v        # Start with debug logging
crewmatic init          # Manual — creates default crew.yaml + .env interactively
crewmatic validate      # Check crew.yaml without starting
crewmatic agents        # List configured agents
crewmatic tasks         # Show task board
crewmatic doctor        # Check prerequisites
```

## Integrations (24 services)

Three tiers — zero to full setup:

| Tier | Setup | Examples |
|------|-------|----------|
| **Claude.ai Connectors** | Connect once in claude.ai | Gmail, Notion, Figma, Canva, Gamma, Calendar, PostHog, Cloudflare, Miro, Granola |
| **CLI tools** | Token in .env | GitHub (`gh`), AWS (`aws`), Stripe (`stripe`) |
| **Local MCP** | Auto-configured | PostgreSQL, custom servers |

### How integrations work

Crewmatic agents run via Claude CLI on your machine. When an agent needs to send an email or read a Notion page, it uses Claude's built-in connectors — the same ones you see in [claude.ai](https://claude.ai).

**Claude.ai Connectors** (Gmail, Notion, Figma, etc.) — your agents use whatever services you've connected in your Claude account. No API keys needed. To set up:

1. Go to [claude.ai/settings](https://claude.ai/settings) → **Integrations**
2. Connect the services you want (Gmail, Notion, Google Calendar, etc.)
3. That's it — any agent with that integration in `crew.yaml` gets automatic access

Your agents will be able to read emails, create drafts, search Notion, create Canva designs, and more — all through your connected accounts.

**CLI tools** (GitHub, AWS, Stripe) — these need API tokens because agents use the actual CLI tools (`gh`, `aws`, `stripe`). The setup wizard asks for these during `crewmatic setup`.

**Local MCP** (PostgreSQL, custom) — auto-spawned MCP server processes for direct database access.

Agents automatically get the right tools based on their role and integrations configured in `crew.yaml`.

## Configuration

Everything lives in `crew.yaml`:

```yaml
name: "My AI Company"

slack:
  app_token: ${SLACK_APP_TOKEN}
  bot_token: ${SLACK_BOT_TOKEN}

owner:
  slack_id: ${OWNER_SLACK_ID}

integrations: [github, gmail, notion, figma, canva, gamma]

agents:
  ceo:
    channel: "ceo"
    model: "opus"
    role: "leader"
    delegates_to: [cto, cmo]
    system_prompt: |
      You are the CEO. Run this company autonomously...

  cto:
    channel: "cto"
    model: "opus"
    role: "manager"
    reports_to: ceo
    integrations: [github]
    system_prompt: |
      You are the CTO. Own all technical decisions...

  cmo:
    channel: "cmo"
    model: "sonnet"
    role: "worker"
    reports_to: ceo
    integrations: [gmail, canva, gamma, figma]
    system_prompt: |
      You are the CMO. Marketing, growth, content...

projects:
  my-app:
    name: "My App"
    codebase: "/path/to/repo"
```

### Agent roles

| Role | What it does |
|------|-------------|
| `leader` | Plans work, delegates, sends reports, hires new agents |
| `manager` | Reviews worker output (approve/reject), can hire sub-team |
| `worker` | Picks tasks from board, executes, reports results |

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `max_concurrent_agents` | 4 | Max parallel Claude CLI processes |
| `worker_poll_interval` | 60 | Seconds between task board checks |
| `planning_interval` | 1800 | Seconds between planning runs |
| `planning_threshold` | 3 | Plan more when open tasks < this |
| `report_hours` | [9, 16, 22] | Hours for scheduled reports |
| `stuck_timeout_minutes` | 10 | Reset stuck tasks after this |

## Architecture

```
crewmatic/
├── bot.py             # Slack orchestration — wires everything together
├── scheduler.py       # Planning, worker, and report loops
├── delegation.py      # @agent: task parsing + auto-hire detection
├── task_manager.py    # JSON task board with atomic operations
├── cost_tracker.py    # Per-agent cost tracking
├── integrations.py    # 24-service catalog (Claude.ai MCP + CLI + local)
├── claude_runner.py   # Claude CLI subprocess with concurrency control
├── context.py         # Memory + context injection
├── agent_loader.py    # crew.yaml → AgentConfig
├── config.py          # YAML loading + validation
├── project_manager.py # Multi-project context switching
├── guardrails.py      # Circuit breaker + execution guard
├── workflows.py       # Multi-step workflow pipelines
└── onboarding/        # Slack setup wizard + crew generator
```

## Safety

- **Circuit breaker** — agents auto-pause after consecutive failures
- **Manager review gate** — worker output verified before marking complete
- **Escalation** — workers flag blockers instead of looping
- **Cost tracking** — per-agent, per-day spend estimates in reports
- **Loop prevention** — cooldowns between bot messages

When agents have `tools` configured, Crewmatic passes `--dangerously-skip-permissions` to Claude CLI. Set `skip_permissions: false` in settings to disable.

## Examples

- **[startup](examples/startup/)** — AI company with CEO, CTO, CMO (dynamic hiring enabled)
- **[dev-team](examples/dev-team/)** — Minimal dev team (Lead, Frontend, Backend)

## License

MIT
