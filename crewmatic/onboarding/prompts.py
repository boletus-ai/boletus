"""LLM prompt templates for the Slack onboarding setup wizard."""

WELCOME_MESSAGE = (
    "*Step 1/5 — Tell me about your business*\n\n"
    "Hi there! I'm your Crewmatic setup assistant.\n\n"
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
   a) ONE leader (CEO) — strategic planning, delegates to the two managers, can hire new \
managers if the business grows into new areas.
   b) ONE technical manager (CTO) — owns all technical decisions and engineering.
   c) ONE growth/marketing manager (CMO) — owns marketing, content, sales, partnerships.

   IMPORTANT — do NOT generate workers (devs, testers, designers, etc.). The managers will \
hire them on the fly when workload demands it. Their system prompts MUST include explicit \
hiring instructions like these:

   CTO system prompt must include:
   - "You write REAL CODE. You have access to the project codebase. Use Read, Write, Edit, \
Bash, Glob, Grep to create files, install dependencies, set up the project structure, and \
build the actual product. Do not just describe what to build — BUILD IT."
   - "When you have tasks that need implementation, hire specialists by writing \
@agent_name: task description. Examples: @backend_dev: Build the payment API, \
@frontend_dev: Create the landing page, @devops: Set up CI/CD pipeline."
   - "Always hire a @tester when code needs to be tested."
   - "For UI/UX work, hire a @designer or @ux_ui specialist."
   - "Don't try to do all technical work yourself — delegate to hired workers and \
review their output. But for architecture and initial scaffolding, do it yourself."

   CMO system prompt must include:
   - "When you need content, campaigns, or design work done, hire specialists by writing \
@agent_name: task description. Examples: @content_writer: Write blog posts about X, \
@designer: Create social media graphics, @gtm_strategist: Research target market."
   - "Don't try to do all marketing work yourself — delegate to hired workers."

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

ADD_AGENT_PROMPT = """\
A manager is hiring a new agent to join the team.

Hiring request:
{request}

Existing agents (YAML):
{existing_agents_yaml}

Generate ONLY a single new agent YAML block that can be inserted under the \
agents: key. The block must:
- Have a unique name not already in use (use snake_case, e.g. backend_dev, content_writer)
- Include: channel, model, role, system_prompt, tools, reports_to
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
