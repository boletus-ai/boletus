"""Crewmatic CLI — init, run, validate, agents, tasks."""

import argparse
import logging
import os
import sys

from . import __version__

logger = logging.getLogger(__name__)

SCAFFOLD_CREW_YAML = '''# Crewmatic — Your AI Company
# Starts with CEO, CTO, CMO. They hire more agents as needed.
# Docs: https://github.com/Majny/crewmatic

name: "My AI Company"

slack:
  app_token: ${SLACK_APP_TOKEN}
  bot_token: ${SLACK_BOT_TOKEN}

owner:
  slack_id: ${OWNER_SLACK_ID}

settings:
  max_concurrent_agents: 4
  report_hours: [9, 16, 22]

data_dir: "./data"
memory_dir: "./memory"
context_dir: "./context"

git:
  author_name: "AI CTO"
  author_email: "ai-cto@example.com"

integrations: [github, figma, canva, gamma]

agents:
  ceo:
    channel: "ceo"
    model: "opus"
    role: "leader"
    tools: "Read,Write,Edit,WebFetch,WebSearch,Glob,Grep"
    delegates_to: [cto, cmo]
    system_prompt: |
      You are the CEO of this company. You operate fully autonomously.
      The owner is your investor/board — they receive reports, not manage you.

      WHEN YOU RECEIVE A BUSINESS PLAN OR VISION:
      1. Analyze it immediately — identify the core product, target market, and first milestone
      2. Break it into concrete, delegatable tasks for your team RIGHT NOW
      3. Start with the highest-impact work first (MVP, not perfection)
      4. Delegate at least 3-5 specific tasks in your FIRST response
      5. Save key context to your memory file for future reference

      YOUR RESPONSIBILITIES:
      - Set strategic direction and priorities
      - Delegate to your team with specific, measurable objectives
      - Make GO/NO-GO decisions on major initiatives
      - Report progress, costs, and key metrics to the owner
      - HIRE new team members when workload demands it

      YOUR STARTING TEAM:
      - @cto: All technical decisions, architecture, code, GitHub repos
      - @cmo: Marketing, growth, customer acquisition, content

      HIRING — Create new roles on the fly:
      Just delegate to a role that doesn't exist yet:
        @cpo: Define the product roadmap and user stories for our MVP
        @cfo: Build a financial model — costs, revenue projections, runway
        @sales_rep: Research 50 target companies and start cold outreach

      Only hire when the workload justifies it. Start lean, grow as needed.

      DELEGATION RULES:
      - Be specific: what to build, acceptance criteria, what "done" looks like
      - Prefer small, shipped iterations over big plans
      - Don't delegate vague tasks like "think about X" — delegate concrete outputs

      SELF-CORRECTION:
      - If tasks keep failing, reassess the strategy
      - Escalate to the owner only for major pivots

      To delegate: @agent: specific task with clear deliverables

  cto:
    channel: "cto"
    model: "opus"
    role: "manager"
    tools: "Read,Glob,Grep,Bash,Edit,Write,WebFetch,WebSearch"
    delegates_to: []
    reports_to: ceo
    integrations: [github]
    system_prompt: |
      You are the CTO. You own all technical decisions and code quality.

      YOUR RESPONSIBILITIES:
      - Architecture decisions and technical direction
      - Code review — no code ships without your approval
      - Breaking features into implementable tasks
      - Ensuring test coverage and code quality
      - HIRING developers and testers when you need them

      HIRING — Grow your team as needed:
        @backend_dev: Implement the authentication module
        @frontend_dev: Build the landing page with React
        @tester: Write integration tests for the API
        @devops: Set up CI/CD pipeline

      GIT WORKFLOW:
      1. Create feature branches: git checkout -b feature/short-description
      2. Delegate implementation to developers
      3. After implementation, delegate testing
      4. Review code, merge if good, send back if not

      CODE QUALITY RULES:
      - Read existing code before writing new code
      - Follow existing patterns and conventions
      - Every feature needs tests
      - No hardcoded secrets — use environment variables

  cmo:
    channel: "cmo"
    model: "sonnet"
    role: "worker"
    tools: "Read,Write,Edit,WebFetch,WebSearch,Glob,Grep"
    reports_to: ceo
    integrations: [canva, gamma, figma]
    system_prompt: |
      You are the CMO reporting to the CEO.

      YOUR RESPONSIBILITIES:
      - Market research and competitive analysis
      - Content creation (blog posts, landing pages, social copy)
      - Growth strategy and customer acquisition
      - Creating visual content with Canva, Figma, and Gamma

      WORKFLOW:
      1. Research before creating — understand the market and audience
      2. Use WebSearch to find real data and trends
      3. Use Canva/Gamma for presentations and visual content
      4. Track what you've done in your memory file

      RULES:
      - Every piece of content must have a clear goal and target audience
      - Back claims with data from research
      - Write content ready to publish, not drafts with [TODO] placeholders

projects:
  my-project:
    name: "My Project"
    description: "Describe your project here"
    codebase: "."
    context: |
      Add project context here — tech stack, current status, priorities.

# Custom MCP servers — add any MCP server your agents should have access to.
# These are passed to Claude CLI via --mcp-config and available to ALL agents.
# mcp_servers:
#   my-custom-server:
#     command: "npx"
#     args: ["-y", "@my-org/my-mcp-server"]
#     env:
#       API_KEY: "${MY_API_KEY}"
'''


def _prompt(message: str, required: bool = True, secret: bool = False) -> str:
    """Prompt user for input with optional masking."""
    import getpass
    while True:
        if secret:
            value = getpass.getpass(f"  {message}: ")
        else:
            value = input(f"  {message}: ").strip()
        if value or not required:
            return value
        print("    This field is required. Try again.")


def cmd_init(args):
    """Interactive setup — creates .env and crew.yaml."""
    print()
    print("=== Crewmatic Setup ===")
    print()

    # Step 1: Slack app
    env_path = os.path.join(os.getcwd(), ".env")
    env_vars = {}

    if os.path.exists(env_path) and not args.force:
        from dotenv import dotenv_values
        env_vars = dict(dotenv_values(env_path))
        if env_vars.get("SLACK_BOT_TOKEN") and env_vars.get("SLACK_APP_TOKEN"):
            print("Found existing .env with Slack tokens.")
            print()

    if not env_vars.get("SLACK_BOT_TOKEN"):
        print("Step 1: Create a Slack app")
        print()
        print("  1. Go to https://api.slack.com/apps")
        print("  2. Click 'Create New App' > 'From a manifest'")
        print("  3. Select your workspace")
        print("  4. Paste the contents of slack-app-manifest.json")
        print("     (find it in the crewmatic repo root)")
        print("  5. Click 'Create'")
        print()
        print("Step 2: Get your tokens")
        print()
        print("  App Token: Basic Information > scroll to 'App-Level Tokens'")
        print("  > Generate Token and Scopes > add 'connections:write' > Generate")
        app_token = _prompt("Paste your App Token (xapp-...)", secret=True)
        env_vars["SLACK_APP_TOKEN"] = app_token
        print()
        print()
        print("  Bot Token: Install App (left menu) > Install to Workspace > copy Bot Token")
        bot_token = _prompt("Paste your Bot Token (xoxb-...)", secret=True)
        env_vars["SLACK_BOT_TOKEN"] = bot_token
        print()

    if not env_vars.get("OWNER_SLACK_ID"):
        print("Step 3: Your Slack User ID")
        print("  (Click your profile in Slack > three dots > 'Copy member ID')")
        owner_id = _prompt("Paste your Member ID (U...)")
        env_vars["OWNER_SLACK_ID"] = owner_id
        print()

    # Optional: GitHub token
    if not env_vars.get("GITHUB_TOKEN"):
        print("Step 4: GitHub token (optional — needed if agents should code)")
        print()
        print("  1. Go to https://github.com/settings/tokens")
        print("  2. Click 'Generate new token' > 'Generate new token (classic)'")
        print("  3. Name: crewmatic, Expiration: pick what suits you")
        print("  4. Select scopes: repo (full), workflow")
        print("     (just check these two boxes, leave everything else unchecked)")
        print("  5. Click 'Generate token' > copy the ghp_... value")
        print()
        gh_token = _prompt("Paste GitHub token (or press Enter to skip)", required=False, secret=True)
        if gh_token:
            env_vars["GITHUB_TOKEN"] = gh_token
        print()

    # Write .env
    with open(env_path, "w") as f:
        f.write("# Crewmatic — generated by crewmatic init\n")
        for key, val in env_vars.items():
            if val and not (val.startswith('"') or val.startswith("'")):
                val = f'"{val}"'
            f.write(f"{key}={val}\n")
    print(f"Saved tokens to .env")

    # Step 5: crew.yaml
    target = os.path.join(os.getcwd(), "crew.yaml")
    if os.path.exists(target) and not args.force:
        print(f"crew.yaml already exists (use --force to overwrite).")
    else:
        with open(target, "w") as f:
            f.write(SCAFFOLD_CREW_YAML)
        print("Created crew.yaml")

    # Create directories
    for d in ("data", "memory", "context"):
        os.makedirs(d, exist_ok=True)

    print()
    print("Setup complete! Next:")
    print("  1. Edit crew.yaml to customize your team (optional)")
    print("  2. Run: crewmatic run")
    print("  3. Go to #ceo in Slack and send your business plan")
    print()
    return 0


def cmd_setup(args):
    """Start Slack-based setup wizard."""
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import os
    from dotenv import load_dotenv
    load_dotenv()

    config_dir = os.getcwd()
    if args.config:
        config_dir = os.path.dirname(os.path.abspath(args.config))

    # Determine LLM runner
    from .claude_runner import ClaudeRunner
    llm = ClaudeRunner(max_concurrent=1, timeout=300)

    from .onboarding import SetupWizard
    from slack_bolt import App

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    owner_id = os.environ.get("OWNER_SLACK_ID", "")

    if not bot_token or not app_token:
        print("Error: SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set.")
        print("Run: crewmatic doctor")
        return 1

    app = App(token=bot_token)

    def on_complete(config_path, business_description=""):
        print(f"\nSetup complete! Config saved to {config_path}")
        print("Starting your AI team...")
        from .bot import CrewmaticBot
        bot = CrewmaticBot(config_path=config_path)
        if business_description:
            bot.queue_business_plan(business_description)
        bot.start()

    wizard = SetupWizard(
        app=app,
        app_token=app_token,
        config_dir=config_dir,
        llm_runner=llm,
        owner_slack_id=owner_id,
        on_complete=on_complete,
    )
    wizard.start()
    return 0


def cmd_run(args):
    """Start the bot."""
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        from .bot import CrewmaticBot
        bot = CrewmaticBot(config_path=args.config)
        bot.start()
    except FileNotFoundError:
        print("No crew.yaml found. Starting setup wizard...")
        print("(You can also run: crewmatic init  for manual YAML setup)")
        print()
        return cmd_setup(args)
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

    # Integration credentials
    if config_path:
        try:
            from .config import load_config
            config = load_config(str(config_path))
            from .integrations import check_integration_credentials
            integrations = config.get("integrations", [])
            if integrations:
                cred_checks = check_integration_credentials(integrations)
                for int_name, env_var, is_set in cred_checks:
                    checks.append((is_set, f"{int_name}: {env_var} {'set' if is_set else 'NOT SET'}", f"Required for {int_name} integration"))
        except Exception:
            pass

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

    # setup
    setup_parser = subparsers.add_parser("setup", help="Interactive Slack-based team setup")
    setup_parser.add_argument("-c", "--config", help="Path to output crew.yaml")
    setup_parser.add_argument("-v", "--verbose", action="store_true")

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
        "setup": cmd_setup,
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
