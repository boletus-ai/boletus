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


def cmd_local(args):
    """Start local terminal REPL (no Slack required)."""
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from .local_runner import start_local
    start_local(config_path=args.config)
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


def cmd_doctor(args):
    """Check prerequisites and environment setup."""
    import shutil
    import sys as _sys

    checks = []

    # Python version
    py_ver = _sys.version_info
    ok = py_ver >= (3, 11)
    checks.append((ok, f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}", ">= 3.11 required"))

    # Claude CLI
    claude_path = shutil.which("claude")
    checks.append((bool(claude_path), f"Claude CLI: {claude_path or 'NOT FOUND'}", "Install: https://docs.anthropic.com/en/docs/claude-code"))

    # crew.yaml
    from .config import find_config
    config_path = find_config()
    checks.append((bool(config_path), f"crew.yaml: {config_path or 'NOT FOUND'}", "Run: crewmatic init"))

    # Slack tokens
    slack_bot = os.environ.get("SLACK_BOT_TOKEN", "")
    checks.append((bool(slack_bot), f"SLACK_BOT_TOKEN: {'set' if slack_bot else 'NOT SET'}", "Get from Slack app settings"))

    slack_app = os.environ.get("SLACK_APP_TOKEN", "")
    checks.append((bool(slack_app), f"SLACK_APP_TOKEN: {'set' if slack_app else 'NOT SET'}", "Enable Socket Mode in Slack app"))

    owner = os.environ.get("OWNER_SLACK_ID", "")
    checks.append((bool(owner), f"OWNER_SLACK_ID: {'set' if owner else 'NOT SET'}", "Your Slack user ID"))

    # Dependencies
    for pkg_name, import_name in [("slack-bolt", "slack_bolt"), ("pyyaml", "yaml"), ("python-dotenv", "dotenv")]:
        try:
            __import__(import_name)
            checks.append((True, f"Package {pkg_name}: installed", ""))
        except ImportError:
            checks.append((False, f"Package {pkg_name}: NOT INSTALLED", f"pip install {pkg_name}"))

    # Config validation (if config exists)
    if config_path:
        try:
            from .config import load_config
            from .agent_loader import load_agents
            config = load_config(str(config_path))
            agents = load_agents(config)
            checks.append((True, f"Config valid: {len(agents)} agents defined", ""))
        except Exception as e:
            checks.append((False, f"Config error: {e}", "Fix crew.yaml"))

    # Print results
    all_ok = True
    for ok, msg, hint in checks:
        icon = "[OK]" if ok else "[!!]"
        if not ok:
            all_ok = False
        line = f"  {icon} {msg}"
        if not ok and hint:
            line += f"  ({hint})"
        print(line)

    print()
    if all_ok:
        print("All checks passed! Run: crewmatic run")
    else:
        print("Some checks failed. Fix the issues above, then run: crewmatic doctor")

    return 0 if all_ok else 1


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

    # local
    local_parser = subparsers.add_parser("local", help="Start local terminal REPL (no Slack)")
    local_parser.add_argument("-c", "--config", help="Path to crew.yaml")
    local_parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

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

    # doctor
    subparsers.add_parser("doctor", help="Check prerequisites and environment")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "init": cmd_init,
        "run": cmd_run,
        "local": cmd_local,
        "validate": cmd_validate,
        "agents": cmd_agents,
        "tasks": cmd_tasks,
        "doctor": cmd_doctor,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
