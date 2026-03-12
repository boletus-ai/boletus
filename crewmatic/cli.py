"""Crewmatic CLI — init, run, validate, agents, tasks."""

import argparse
import logging
import os
import sys

from . import __version__

logger = logging.getLogger(__name__)

SCAFFOLD_CREW_YAML = '''# Crewmatic — Your first AI company
# Docs: https://github.com/Majny/crewmatic

name: "My AI Company"

# Slack configuration
slack:
  app_token: ${SLACK_APP_TOKEN}
  bot_token: ${SLACK_BOT_TOKEN}

# Owner (receives reports, can override decisions)
owner:
  slack_id: ${OWNER_SLACK_ID}

# Settings (all optional — these are defaults)
settings:
  max_concurrent_agents: 4
  worker_poll_interval: 60
  planning_interval: 1800
  planning_cooldown: 600
  planning_threshold: 3
  report_hours: [9, 16, 22]
  stuck_timeout_minutes: 10

# Where data is stored (relative to this file)
data_dir: "./data"
memory_dir: "./memory"
context_dir: "./context"

# Git identity for agent commits (optional)
git:
  author_name: "AI Agent"
  author_email: "ai@example.com"

# Agent definitions
agents:
  lead:
    channel: "lead"
    model: "opus"
    role: "leader"
    tools: "Read,Write,Edit,WebFetch,WebSearch,Glob,Grep"
    delegates_to: [developer, marketer]
    system_prompt: |
      You are the Lead of this AI company. You run it autonomously.

      Your responsibilities:
      - Strategic planning and prioritization
      - Delegating tasks to your team
      - Reporting progress to the owner

      Your team:
      - @developer: handles all technical work
      - @marketer: handles growth, marketing, outreach

      To delegate, use: @developer: task description
      Be specific. Don't repeat tasks already on the board.

  developer:
    channel: "developer"
    model: "sonnet"
    role: "worker"
    tools: "Read,Glob,Grep,Bash,Edit,Write,WebFetch,WebSearch"
    reports_to: lead
    system_prompt: |
      You are a Developer. You report to the Lead.

      Your responsibilities:
      - Implement features and fix bugs
      - Write clean, tested code
      - Report what you did after completing a task

  marketer:
    channel: "marketer"
    model: "sonnet"
    role: "worker"
    tools: "Read,Write,Edit,WebFetch,WebSearch,Glob,Grep"
    reports_to: lead
    system_prompt: |
      You are a Marketer. You report to the Lead.

      Your responsibilities:
      - Research markets and competitors
      - Create marketing content and strategies
      - Find and reach potential customers
      - Report findings after completing a task

# Projects (optional — for multi-project teams)
projects:
  my-project:
    name: "My Project"
    description: "Describe your project here"
    codebase: "."
    context: |
      Add project context here — tech stack, current status, priorities.
'''


def cmd_init(args):
    """Create a new crew.yaml scaffold."""
    target = os.path.join(os.getcwd(), "crew.yaml")
    if os.path.exists(target) and not args.force:
        print("crew.yaml already exists. Use --force to overwrite.")
        return 1

    with open(target, "w") as f:
        f.write(SCAFFOLD_CREW_YAML)

    # Create directories
    for d in ("data", "memory", "context"):
        os.makedirs(d, exist_ok=True)

    print("Created crew.yaml + data/ memory/ context/ directories.")
    print("")
    print("Next steps:")
    print("  1. Edit crew.yaml — define your agents and projects")
    print("  2. Create a Slack app (Socket Mode) and get tokens")
    print("  3. Set environment variables: SLACK_BOT_TOKEN, SLACK_APP_TOKEN, OWNER_SLACK_ID")
    print("  4. Create Slack channels matching your agent config")
    print("  5. Run: crewmatic run")
    return 0


def cmd_run(args):
    """Start the bot."""
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from .bot import CrewmaticBot
    bot = CrewmaticBot(config_path=args.config)
    bot.start()
    return 0


def cmd_validate(args):
    """Validate crew.yaml without starting."""
    logging.basicConfig(level=logging.WARNING)

    from .config import load_config
    from .agent_loader import load_agents

    try:
        config = load_config(args.config)
        agents = load_agents(config)
        print(f"crew.yaml is valid.")
        print(f"  Name: {config.get('name')}")
        print(f"  Agents: {len(agents)}")
        for name, agent in agents.items():
            print(f"    {name} (role={agent.role}, model={agent.model}, channel=#{agent.channel})")
        projects = config.get("projects", {})
        if projects:
            print(f"  Projects: {len(projects)}")
            for key, proj in projects.items():
                print(f"    {key}: {proj.get('name', key)}")
        return 0
    except Exception as e:
        print(f"Validation failed: {e}")
        return 1


def cmd_agents(args):
    """List configured agents."""
    logging.basicConfig(level=logging.WARNING)

    from .config import load_config
    from .agent_loader import load_agents

    try:
        config = load_config(args.config)
        agents = load_agents(config)
        for name, agent in agents.items():
            delegates = ", ".join(agent.delegates_to) if agent.delegates_to else "none"
            print(f"{name}")
            print(f"  role: {agent.role}")
            print(f"  model: {agent.model}")
            print(f"  channel: #{agent.channel}")
            print(f"  delegates to: {delegates}")
            print(f"  reports to: {agent.reports_to or 'none'}")
            print()
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def cmd_tasks(args):
    """Show task board."""
    logging.basicConfig(level=logging.WARNING)

    from .config import load_config
    from .task_manager import TaskManager

    try:
        config = load_config(args.config)
        tm = TaskManager(data_dir=config["data_dir"])
        print(tm.get_summary(include_done=args.all))
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        prog="crewmatic",
        description="Crewmatic — Your first AI company",
    )
    parser.add_argument("--version", action="version", version=f"crewmatic {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    # init
    init_parser = subparsers.add_parser("init", help="Create a new crew.yaml scaffold")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing crew.yaml")

    # run
    run_parser = subparsers.add_parser("run", help="Start the bot")
    run_parser.add_argument("-c", "--config", help="Path to crew.yaml")
    run_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    # validate
    val_parser = subparsers.add_parser("validate", help="Validate crew.yaml")
    val_parser.add_argument("-c", "--config", help="Path to crew.yaml")

    # agents
    agents_parser = subparsers.add_parser("agents", help="List configured agents")
    agents_parser.add_argument("-c", "--config", help="Path to crew.yaml")

    # tasks
    tasks_parser = subparsers.add_parser("tasks", help="Show task board")
    tasks_parser.add_argument("-c", "--config", help="Path to crew.yaml")
    tasks_parser.add_argument("-a", "--all", action="store_true", help="Include completed tasks")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "init": cmd_init,
        "run": cmd_run,
        "validate": cmd_validate,
        "agents": cmd_agents,
        "tasks": cmd_tasks,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
