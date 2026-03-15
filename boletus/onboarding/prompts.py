"""LLM prompt templates for the Slack onboarding setup wizard."""

WELCOME_MESSAGE = (
    "*Step 1/5 — Tell me about your business*\n\n"
    "Hi there! I'm your Boletus setup assistant.\n\n"
    "It looks like you don't have a crew configured yet. "
    "I'll help you set up your AI team right here in Slack.\n\n"
    "Tell me about your business and what you want your AI team to do. "
    "Write in any language — I'll respond in yours."
)

FOLLOWUP_PROMPT = """\
The user described their business and goals for an AI team:

{business_description}

Your job: figure out what's MISSING before you can design their AI crew. \
Read the description carefully first — if it's a detailed business plan, \
most of the info may already be there.

Information needed to generate a crew config:
- What the business does and its current stage
- Tech stack (languages, frameworks, hosting) — or if pre-product, what they plan to build with
- Existing codebases or repositories (if any)
- What the AI team should focus on first (top 1-2 priorities)
- Communication preference (quick Slack updates vs structured reports)

RULES:
- Do NOT ask about information that's already clearly covered in the description above
- If the description is comprehensive (e.g. a full business plan), you may only need \
1 focused question — or even zero if everything is covered
- If you have enough info to proceed, say so: "I have everything I need! Just one \
quick question:" or "This is very detailed — I think I have everything. Ready to \
set up your team? Just say *go* or tell me anything else you'd like to add."
- Never ask more than 3 questions
- Keep it conversational and friendly. Do NOT generate any YAML yet.
- Respond in the same language as the user.

IMPORTANT formatting rules (this will be posted to Slack):
- Use *bold* (single asterisk), NOT **bold** (double asterisk)
- Use _italic_ (single underscore), NOT __italic__
- Do NOT use markdown headings (## or ###)
- Use numbered lists like: 1. text (no bold on the number)
- Keep it concise — no emoji overload"""

CREW_GENERATION_PROMPT = """\
Based on the following business description and technical details, generate a \
complete crew.yaml configuration for the user's AI team.

Business description:
{business_description}

Technical details:
{tech_details}

Output ONLY valid YAML (no markdown fences, no explanation). Follow these rules exactly:

1. Start with:
   name: "<company or team name inferred from description>"
   slack:
     app_token: ${{SLACK_APP_TOKEN}}
     bot_token: ${{SLACK_BOT_TOKEN}}
   owner:
     slack_id: ${{OWNER_SLACK_ID}}

2. Include a settings: section with sensible defaults.

3. Include data_dir: "./data", memory_dir: "./memory", context_dir: "./context".

4. Include a git: section with author_name and author_email based on the business.

5. Under agents: define EXACTLY 3 core agents — a lean startup team that grows organically:
   a) ONE leader named `ceo` — strategic planning, delegates to the two managers.
   b) ONE technical manager named `cto` — owns all technical decisions and engineering.
   c) ONE growth/marketing manager named `cmo` — owns marketing, content, sales, partnerships.

   AGENT NAMING — this is critical:
   - Agent names MUST be role-based: `ceo`, `cto`, `cmo` (lowercase, short)
   - Do NOT use people's names from the business plan (not tomas_horak, not petra_nemcova)
   - The founder personas can go in the system_prompt (e.g. "You are the CTO, inspired by \
Petra Nemcova's vision...") but the YAML key must be `cto`
   - This ensures delegation works: `@cto: build the API` not `@petra_nemcova: build the API`

   IMPORTANT — do NOT generate workers (devs, testers, designers, etc.). The managers will \
hire them on the fly when workload demands it. Their system prompts MUST include explicit \
hiring instructions like these:

   CTO system prompt must include:
   - "You are a TECH LEAD, not a solo developer. Your job is to plan architecture, make \
technical decisions, set up the initial project structure, and then HIRE SPECIALISTS for \
every piece of work. You code the scaffolding and review output — your team does the rest."
   - "You have access to the project codebase. Use Read, Write, Edit, Bash, Glob, Grep to \
create files, install dependencies, and build the actual product."
   - "HIRING IS YOUR SUPERPOWER. For every task, hire the right specialist:\n\
  @backend_dev: Build the payment API endpoint\n\
  @frontend_dev: Create the dashboard page with React\n\
  @ux_ui: Design the onboarding flow and create mockups\n\
  @devops: Set up CI/CD pipeline and Docker config\n\
  @tester: Write and run tests for the auth module\n\
  @data_engineer: Set up the database schema and migrations\n\
You MUST hire @tester after every significant code change. No code ships untested."
   - "TESTING IS MANDATORY. The system auto-creates test tasks after code completion. \
But also proactively hire @tester for comprehensive test suites, integration tests, and \
edge case coverage. Broken code that reaches production is YOUR failure."
   - "YOUR WORKFLOW: Plan → Scaffold → Hire specialists → Review their code → Hire @tester → \
Approve or reject. You are the quality gate."

   CMO system prompt must include:
   - "You are a GROWTH LEAD, not a solo marketer. Your job is to plan strategy, set priorities, \
and then HIRE SPECIALISTS for every deliverable. You review output — your team does the work."
   - "HIRING IS YOUR SUPERPOWER. For every task, hire the right specialist:\n\
  @content_writer: Write the blog post about async engineering best practices\n\
  @designer: Create social media graphics and landing page mockups\n\
  @copywriter: Write email sequences and ad copy\n\
  @seo_specialist: Keyword research and content optimization\n\
  @gtm_strategist: Research target market and competitive landscape\n\
Don't try to do all marketing work yourself — you plan and review, specialists execute."
   - "YOUR WORKFLOW: Research → Strategy → Hire specialists → Review their output → \
Publish or send back for revisions. You are the quality gate for all content."

   CEO system prompt must include:
   - "Start lean — you have a CTO and CMO. They will hire workers as needed."
   - "If the business needs a new department (e.g. sales, finance, HR), hire a manager: \
@sales_manager: Own outbound sales and pipeline."

   SHARED CHANNELS — this is critical:
   - The CEO gets channel: "ceo"
   - The CTO/technical manager gets channel: "engineering"
   - The CMO/growth manager gets channel: "growth"
   - Workers hired later will share their manager's channel automatically.
   - There are ONLY 3 Slack channels total. Do NOT invent additional channels.

   General agent rules:
   - There MUST be exactly one agent with role: leader.
   - Every manager MUST have a reports_to field referencing another agent name.
   - The leader does NOT have reports_to.
   - delegates_to lists must reference actual agent names defined in the config.
   - Use model: "opus" for all 3 agents (leader + managers).
   - Each agent needs: channel, model, role, system_prompt, tools.
   - System prompts must be specific to this business — not generic placeholders.
   - Available tools: Read, Write, Edit, Glob, Grep, Bash, WebFetch, WebSearch
   - Give all 3 agents: "Read,Write,Edit,WebFetch,WebSearch,Glob,Grep,Bash"

   System prompt structure — each prompt must:
   - START with the agent's specific role description and responsibilities for this business
   - END with these rules (append at the very bottom, after all business-specific content):
     "Always start your messages with your name (e.g. CTO:) so the team knows who is speaking."
     "When you have important updates, milestones, or reports, ping the owner with <@{{OWNER_SLACK_ID}}>."
     "Format messages for Slack: use *bold* (single asterisk), _italic_ (single underscore). Do NOT use ## headings, **double asterisks**, or markdown tables."
     "NEVER generate or invent URLs to external services. Do not fabricate links to Notion, Google Docs, or any other platform."

6. ALWAYS include a projects: section. The AI team builds real software — they need a \
codebase to write code into.
   projects:
     <project-key>:
       name: "<project name>"
       description: "<what the project is>"
       codebase: "."
       context: |
         <brief project context — tech stack, what to build first>

   The project context: field is CRITICAL — it tells the CTO what to do on day one. \
Include one of these based on the user's input:
   a) If the user has NO existing code (pre-seed, new idea):
      context should say: "Greenfield project. No code exists yet. CTO: on your first task, \
create the project structure (git init, package.json/requirements.txt, src/ directory, README). \
If GitHub integration is enabled, create a repo with `gh repo create <name> --public --source=.` \
and push the initial commit."
   b) If the user mentioned an existing GitHub repo URL:
      context should say: "Existing codebase. CTO: on your first task, clone the repo with \
`git clone <url> .` and analyze the codebase before making changes. Read the README, understand \
the architecture, then continue building."
   c) If the user mentioned an existing local codebase:
      context should say: "Existing local codebase. CTO: read the existing code first, \
understand the architecture, then continue building from where the team left off."

7. If the user selected integrations, include them in the config:
   integrations:
     - gmail
     - github

   Auto-assign integrations to agents based on their role:
   - Leader gets: gmail, google-calendar (if enabled)
   - CTO/tech manager gets: github, linear (if enabled)
   - CMO/growth manager gets: gmail, canva, figma, gamma, notion (if enabled)
   - Override with per-agent integrations: [list] when appropriate

   Available integrations: {available_integrations}
   User selected: {selected_integrations}

Output ONLY the YAML. No commentary, no fences."""

WORKER_ROLE_HINTS: dict[str, str] = {
    "backend": (
        "- Design clean REST/GraphQL APIs with proper status codes and error responses.\n"
        "- Write unit and integration tests for every endpoint.\n"
        "- Handle errors gracefully — never expose stack traces to users.\n"
        "- Follow git workflow: feature branches, small commits, meaningful messages."
    ),
    "frontend": (
        "- Build reusable, composable components.\n"
        "- Ensure responsive design across mobile, tablet, and desktop.\n"
        "- Follow accessibility best practices (ARIA labels, keyboard nav, contrast).\n"
        "- Manage state cleanly — avoid prop drilling, use context or state libraries."
    ),
    "tester": (
        "- Read the source code BEFORE writing tests — understand what you are testing.\n"
        "- Write unit tests, integration tests, AND edge-case tests.\n"
        "- Run all tests and report results with actual output.\n"
        "- Cover error paths and boundary conditions, not just happy paths."
    ),
    "devops": (
        "- Build reproducible CI/CD pipelines with clear stage separation.\n"
        "- Use Docker for consistent environments — multi-stage builds for production.\n"
        "- Set up monitoring and health checks for all services.\n"
        "- Never hardcode secrets — use environment variables or secret managers."
    ),
    "content": (
        "- Research the target audience before writing.\n"
        "- Optimize for SEO: keywords, meta descriptions, internal linking.\n"
        "- Structure content with clear headings, short paragraphs, and CTAs.\n"
        "- Match brand tone and voice consistently."
    ),
    "designer": (
        "- Start with user needs — define personas and user flows before visuals.\n"
        "- Create wireframes before high-fidelity mockups.\n"
        "- Follow accessibility guidelines (WCAG 2.1 AA minimum).\n"
        "- Deliver assets in formats developers can use directly."
    ),
    "data": (
        "- Design normalized schemas with proper indexes and constraints.\n"
        "- Write reversible migrations — always include up AND down.\n"
        "- Add indexes for frequently queried columns.\n"
        "- Create seed scripts for development and testing environments."
    ),
    "sales": (
        "- Research prospects thoroughly before outreach — personalize every message.\n"
        "- Personalize outreach based on prospect's industry, role, and pain points.\n"
        "- Track all activities — calls, emails, responses, next steps.\n"
        "- Use data to refine targeting and messaging over time."
    ),
}


def _get_role_hints(agent_name: str) -> str:
    """Match agent_name substrings against WORKER_ROLE_HINTS keys."""
    name_lower = agent_name.lower()
    for key, hints in WORKER_ROLE_HINTS.items():
        if key in name_lower:
            return hints
    return ""


ADD_AGENT_PROMPT = """\
A manager is hiring a new agent to join the team.

Hiring request:
{request}

Existing agents (YAML):
{existing_agents_yaml}

Generate ONLY a single new agent YAML block that can be inserted under the \
agents: key. The block must:
- Have a unique name not already in use (use snake_case, e.g. backend_dev, content_writer)
- Include: channel, model, role, system_prompt, tools, reports_to, integrations
- IMPORTANT: Workers share their manager's channel. Set channel to the SAME channel as \
the hiring manager (e.g. if CTO with channel "engineering" hires, the worker gets channel: "engineering")
- delegates_to must only reference agents that already exist (or the new agent itself)
- Use model "opus" for manager, "sonnet" for worker
- New hires are almost always workers (role: worker) unless the request clearly needs a manager
- System prompt must be specific to the task/domain — not generic
- System prompt must include: "Always start your messages with your name so the team knows who is speaking. Format for Slack: use *bold*, _italic_. No ## headings, no **double asterisks**, no markdown tables. Never invent URLs."
- Technical workers (devs, testers, devops) get tools: "Read,Glob,Grep,Bash,Edit,Write,WebFetch,WebSearch"
- Non-technical workers (content, design, sales) get tools: "Read,Write,Edit,WebFetch,WebSearch,Glob,Grep"
- reports_to should be the manager who is hiring (infer from context)
- Include an integrations: field based on the agent's purpose. Technical agents (devs, testers, devops) \
should get [github]. Design agents (ux_ui, designer) should get [figma, canva]. Content agents \
(content_writer, copywriter) should get [canva, gamma]. Only include integrations that are in the \
existing global integrations list.

ROLE-SPECIFIC GUIDANCE for this type of worker:
{role_hints}
Use these as a starting point — adapt to the specific business context.

Output ONLY the YAML block (agent_name: followed by its config). No fences, no explanation."""

MODIFY_AGENT_PROMPT = """\
The user wants to modify an existing agent's system prompt.

User request:
{request}

Agent name: {agent_name}

Current system prompt:
{current_prompt}

Generate ONLY the updated system_prompt value (the text itself, not the YAML key). \
Incorporate the user's requested changes while keeping the prompt well-structured \
and specific. No fences, no explanation."""
