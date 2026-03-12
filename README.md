# Crewmatic

**Your first AI company** — Open-source multi-agent framework that runs your business autonomously via Slack + Claude.

Define your AI team in a single YAML file. Each agent has a role, tools, and a Slack channel. They plan, delegate, execute, and report — autonomously.

```
pip install crewmatic
crewmatic init
# edit crew.yaml
crewmatic run
```

## How it works

```
┌─────────┐     ┌────────────┐     ┌──────────┐
│  Leader  │────>│ Task Board │────>│ Workers  │
│ (plans)  │     │  (JSON)    │<────│(execute) │
└─────────┘     └────────────┘     └──────────┘
     │                                    │
     └──────── Slack channels ────────────┘
```

1. **Leader** agent plans work and creates tasks on the board
2. **Worker** agents claim tasks, execute them via Claude CLI, post results
3. **Delegation** — agents can delegate sub-tasks to each other (`@agent: task`)
4. **Memory** — each agent has a persistent markdown memory file
5. **Reports** — scheduled progress reports at configured hours

## Quick start

### Prerequisites

- Python 3.11+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Slack workspace with a [Socket Mode app](https://api.slack.com/apis/socket-mode)

### Setup

```bash
mkdir my-ai-company && cd my-ai-company
crewmatic init        # creates crew.yaml + directories
```

Edit `crew.yaml` — define your agents, Slack channels, and projects.

Set environment variables:

```bash
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_APP_TOKEN="xapp-..."
export OWNER_SLACK_ID="U01234567"
```

Create Slack channels matching your agent config (e.g., `#lead`, `#developer`, `#marketer`).

```bash
crewmatic run
```

### Optional: Per-agent bot identities

Each agent can have its own Slack bot identity:

```bash
export SLACK_BOT_TOKEN_CEO="xoxb-..."
export SLACK_BOT_TOKEN_CTO="xoxb-..."
```

## Configuration

Everything lives in `crew.yaml`:

```yaml
name: "My AI Company"

slack:
  app_token: ${SLACK_APP_TOKEN}
  bot_token: ${SLACK_BOT_TOKEN}

owner:
  slack_id: ${OWNER_SLACK_ID}

agents:
  lead:
    channel: "lead"           # Slack channel
    model: "opus"             # Claude model (opus/sonnet/haiku)
    role: "leader"            # leader | manager | worker
    tools: "Read,Write,Edit,WebFetch,WebSearch,Glob,Grep"
    delegates_to: [dev, marketer]
    system_prompt: |
      You are the Lead. You run this company autonomously.
      Delegate tasks using @dev: or @marketer: format.

  dev:
    channel: "dev"
    model: "sonnet"
    role: "worker"
    tools: "Read,Glob,Grep,Bash,Edit,Write"
    reports_to: lead
    system_prompt: |
      You are a Developer. Implement features, fix bugs.

projects:
  my-app:
    name: "My App"
    codebase: "/path/to/project"
    context: |
      Tech stack, current status, priorities...
```

### Agent roles

| Role | What it does |
|------|-------------|
| `leader` | Runs planning loops, creates tasks, sends reports |
| `manager` | Gets team channel visibility, can delegate to sub-team |
| `worker` | Picks tasks from the board, executes, reports results |

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `max_concurrent_agents` | 4 | Max parallel Claude CLI processes |
| `worker_poll_interval` | 60 | Seconds between task board checks |
| `planning_interval` | 1800 | Seconds between planning runs (when board is full) |
| `planning_threshold` | 3 | Plan more work when open tasks < this |
| `report_hours` | [9, 16, 22] | Hours to send scheduled reports |
| `stuck_timeout_minutes` | 10 | Reset stuck tasks after this |

## CLI commands

```bash
crewmatic init              # Create crew.yaml scaffold
crewmatic run               # Start the bot
crewmatic run -v            # Start with debug logging
crewmatic validate          # Check crew.yaml without starting
crewmatic agents            # List configured agents
crewmatic tasks             # Show task board
crewmatic tasks --all       # Include completed tasks
```

## Slack commands

Message any agent channel or mention the bot:

| Command | Description |
|---------|-------------|
| `standup` | Run team standup |
| `report` | Run progress report |
| `tasks` | Show task board |
| `my tasks` | Show tasks for this channel's agent |
| `cancel #42 reason` | Cancel a task |
| `start <project>` | Activate a project |
| `stop` | Stop current project |
| `status` | Show project status |
| `help` | List commands |

## Context injection

Agents automatically receive relevant context:

- **Memory** — persistent per-agent `.md` files in `memory/`
- **Project context** — from `crew.yaml` projects section
- **Task board** — current open tasks
- **Slack channels** — recent messages from team channels (for leaders/managers)
- **Business context** — files in `context/` directory + Slack #context channel

## Examples

See `examples/` for ready-to-use configurations:

- **[startup](examples/startup/)** — Full AI company (9 agents: CEO, CTO, CMO, CPO, CFO, DevOps, Backend, UX/UI, Tester)
- **[dev-team](examples/dev-team/)** — Minimal dev team (3 agents: Lead, Frontend, Backend)

## Architecture

```
crewmatic/
├── config.py          # YAML loading + validation
├── agent_loader.py    # Agent definitions → dataclass
├── claude_runner.py   # Claude CLI subprocess execution
├── context.py         # Memory + Slack + local context injection
├── delegation.py      # @agent: task pattern parsing
├── scheduler.py       # Planning, worker, and report loops
├── task_manager.py    # JSON task board with atomic operations
├── project_manager.py # Multi-project context switching
├── bot.py             # Slack orchestration engine
└── cli.py             # CLI entry point
```

## License

MIT
