# Crewmatic

**Your first AI company** — Open-source multi-agent framework that runs your business autonomously via Slack + Claude.

Tell the bot about your business in Slack. It builds your AI team, creates channels, and starts working — autonomously.

```
pip install crewmatic
crewmatic setup
# The bot DMs you in Slack and walks you through everything
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

## Getting Started

### 1. Install and connect Slack

```bash
pip install crewmatic
```

Create a [Slack Socket Mode app](https://api.slack.com/apis/socket-mode) and set your tokens:

```bash
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_APP_TOKEN="xapp-..."
export OWNER_SLACK_ID="U01234567"
```

You also need [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated, and Python 3.11+.

### 2. Run the setup wizard

```bash
crewmatic setup
```

The bot DMs you in Slack and walks you through everything:

```
You: I run an e-commerce store selling handmade jewelry.
     I need help with product descriptions, SEO, and customer support.

Crewmatic: Great! I'll set up your team. A few questions:
           - Do you have a website/codebase I should know about?
           - Any specific tools or platforms you use?

You: We use Shopify, and our site is mystore.com

Crewmatic: Here's your proposed AI team:

           👑 MANAGER (#manager) — coordinates the team
           ✏️ CONTENT_WRITER (#content-writer) — product descriptions
           🔍 SEO_SPECIALIST (#seo) — search optimization
           💬 SUPPORT_AGENT (#support) — customer inquiries

           [Create this team] [Make changes] [Start over]
```

Hit **Create this team** — Crewmatic creates the Slack channels, generates your `crew.yaml`, and starts the agents.

### 3. Evolve your team

Need more help later? Just ask:

```
@crewmatic: I need a designer
```

The bot adds the agent, creates the channel, and integrates it with your existing team.

## CLI commands

```bash
crewmatic setup             # Slack-guided setup wizard
crewmatic run               # Start the bot (auto-runs setup if no config)
crewmatic run -v            # Start with debug logging
crewmatic init              # Create crew.yaml scaffold (manual setup)
crewmatic validate          # Check crew.yaml without starting
crewmatic agents            # List configured agents
crewmatic tasks             # Show task board
crewmatic tasks --all       # Include completed tasks
crewmatic doctor            # Check prerequisites
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

## Advanced: Manual Configuration

If you prefer to configure everything by hand, skip the wizard and edit `crew.yaml` directly:

```bash
crewmatic init        # creates crew.yaml scaffold
```

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
| `skip_permissions` | true | Pass `--dangerously-skip-permissions` to Claude CLI |

### Optional: Per-agent bot identities

Each agent can have its own Slack bot identity:

```bash
export SLACK_BOT_TOKEN_CEO="xoxb-..."
export SLACK_BOT_TOKEN_CTO="xoxb-..."
```

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

## How is this different from CrewAI?

| | Crewmatic | CrewAI |
|---|-----------|--------|
| **Communication** | Slack (real-time, human-in-the-loop) | In-process Python |
| **LLM** | Claude CLI (all MCP tools, sessions) | OpenAI API / any LLM |
| **Persistence** | JSON task board + markdown memory | In-memory by default |
| **Autonomy** | Fully autonomous loops (planning, execution, reporting) | Single workflow runs |
| **Config** | Single YAML file | Python code |
| **Best for** | Running an autonomous AI team 24/7 | One-off multi-agent workflows |

Crewmatic is designed for **long-running autonomous teams**, not one-shot pipelines. Your agents plan their own work, execute it, report back, and keep going — all visible in Slack.

## Security

When agents have `tools` configured, Crewmatic passes `--dangerously-skip-permissions` to Claude CLI so agents can use Read/Write/Edit/Bash tools without interactive prompts. This means agents have **full access** to the filesystem within their working directory.

To disable this, set `skip_permissions: false` in your `crew.yaml` settings. Note that agents won't be able to use file/shell tools in this mode.

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
