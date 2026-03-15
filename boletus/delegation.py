"""Delegation parsing — extract @agent: task patterns from LLM responses."""

import logging
import re

logger = logging.getLogger(__name__)


def _build_delegation_pattern(agent_names: set[str]) -> re.Pattern:
    """Build a single regex that matches any delegation pattern."""
    name_alts = "|".join(re.escape(n) for n in agent_names)
    # Match: @agent: ..., **AGENT**: ..., *AGENT*: ...
    # Case-insensitive, captures agent name + everything after the colon
    pattern = (
        rf"(?:@({name_alts})\b[:\s]+|"
        rf"\*\*({name_alts})\*\*[:\s]+|"
        rf"\*({name_alts})\*[:\s]+)"
    )
    return re.compile(pattern, re.IGNORECASE)


def parse_delegations(response: str, agent_names: set[str]) -> list[tuple[str, str]]:
    """Parse @agent: task patterns from an LLM response.

    Recognizes patterns like:
        @cto: implement the auth module
        **CTO**: implement the auth module
        @backend_dev: Build the payment API.
          It should support Stripe webhooks
          and return proper error codes.

    Handles multi-line descriptions: continuation lines (not starting
    with a new delegation or bullet) are joined to the previous delegation.

    Args:
        response: The LLM response text.
        agent_names: Set of valid agent names to look for.

    Returns:
        List of (agent_name, task_description) tuples.
    """
    if not agent_names:
        return []

    pattern = _build_delegation_pattern(agent_names)
    name_lookup = {n.lower(): n for n in agent_names}

    # Find all delegation start positions
    matches = list(pattern.finditer(response))
    if not matches:
        return []

    delegations = []
    for i, m in enumerate(matches):
        # Extract the matched agent name from whichever capture group hit
        raw_name = m.group(1) or m.group(2) or m.group(3)
        agent_name = name_lookup.get(raw_name.lower(), raw_name.lower())

        # Text starts after the match, ends at the next delegation or end
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(response)
        raw_text = response[start:end]

        # Clean up: join continuation lines, strip bullets/numbering
        lines = []
        for line in raw_text.split("\n"):
            stripped = line.strip()
            if not stripped:
                break  # blank line = end of this delegation
            # Stop if line looks like a new section/heading (not continuation)
            if stripped.startswith("#") or stripped.startswith("---"):
                break
            lines.append(stripped)

        task_text = " ".join(lines).strip()
        # Remove trailing markdown artifacts
        task_text = task_text.rstrip("*_")

        if len(task_text) > 10:
            delegations.append((agent_name, task_text))

    return delegations


def parse_unknown_delegations(response: str, known_names: set[str]) -> list[tuple[str, str]]:
    """Find delegations to agents that don't exist yet.

    Uses a broad @name: pattern and filters out known agents.
    Returns list of (new_agent_name, task_description) tuples.
    """
    # Match @word: or **word**: or *word*: patterns
    broad_pattern = re.compile(
        r"(?:@(\w+)\b[:\s]+|\*\*(\w+)\*\*[:\s]+|\*(\w+)\*[:\s]+)",
        re.IGNORECASE,
    )
    known_lower = {n.lower() for n in known_names}
    matches = list(broad_pattern.finditer(response))

    results = []
    for i, m in enumerate(matches):
        raw_name = (m.group(1) or m.group(2) or m.group(3)).lower()
        if raw_name in known_lower:
            continue
        # Skip common false positives (words that appear as **word**: in markdown)
        if raw_name in (
            "here", "channel", "everyone", "team", "all", "hire",
            "total", "summary", "note", "notes", "example", "examples",
            "update", "status", "action", "actions", "result", "results",
            "key", "goal", "goals", "priority", "important", "warning",
            "step", "steps", "phase", "option", "options", "next",
            "revenue", "cost", "costs", "budget", "target", "metric",
            "metrics", "timeline", "deadline", "risk", "risks",
        ):
            continue
        # Skip if task text looks like a table row (starts with |)
        start_peek = m.end()
        peek_text = response[start_peek:start_peek + 20].strip()
        if peek_text.startswith("|"):
            continue

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(response)
        lines = []
        for line in response[start:end].split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("---"):
                break
            lines.append(stripped)

        task_text = " ".join(lines).strip().rstrip("*_")
        if len(task_text) > 10:
            results.append((raw_name, task_text))

    return results


def _fuzzy_match(new_title: str, existing_titles: set[str], threshold: float = 0.6) -> bool:
    """Check if new_title is similar enough to any existing title.

    Uses word-level Jaccard similarity. A threshold of 0.6 means 60% of
    words must overlap to be considered a duplicate.
    """
    new_words = set(new_title.lower().split())
    if len(new_words) < 3:
        # Very short titles — fall back to exact match
        return new_title.lower().strip() in existing_titles
    for existing in existing_titles:
        existing_words = set(existing.split())
        if not existing_words:
            continue
        intersection = new_words & existing_words
        union = new_words | existing_words
        similarity = len(intersection) / len(union) if union else 0
        if similarity >= threshold:
            return True
    return False


_PRIORITY_PATTERN = re.compile(
    r"\[?(HIGH|CRITICAL|URGENT|LOW)\]?\s*:?\s*",
    re.IGNORECASE,
)


def _extract_priority(task_desc: str) -> tuple[str, str]:
    """Extract priority from task description if present.

    Returns (cleaned_desc, priority). Priority defaults to "medium".
    Recognizes: [HIGH], [CRITICAL], [URGENT] → "high", [LOW] → "low".
    """
    m = _PRIORITY_PATTERN.search(task_desc[:50])
    if m:
        label = m.group(1).upper()
        cleaned = task_desc[:m.start()] + task_desc[m.end():]
        if label in ("HIGH", "CRITICAL", "URGENT"):
            return cleaned.strip(), "high"
        if label == "LOW":
            return cleaned.strip(), "low"
    return task_desc, "medium"


def _split_title_details(text: str, max_title_len: int = 80) -> tuple[str, str]:
    """Split task text into a short title and remaining details.

    The first sentence (split on '. ', newline, or bullet) becomes the title
    candidate, truncated at the last word boundary before *max_title_len*.
    The rest becomes details.
    """
    if len(text) <= max_title_len:
        return text, ""

    # Find first sentence boundary
    split_pos = len(text)
    for sep in (". ", "\n", "•"):
        idx = text.find(sep)
        if idx != -1 and idx < split_pos:
            split_pos = idx

    title_candidate = text[:split_pos].strip()

    if len(title_candidate) > max_title_len:
        # Truncate at last word boundary
        truncated = title_candidate[:max_title_len]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        title_candidate = truncated.rstrip(".,;:!? ")

    details = text[len(title_candidate):].lstrip(". \n•").strip()
    return title_candidate, details


def handle_delegations(
    source_agent: str,
    response: str,
    agent_names: set[str],
    add_task_fn,
    existing_tasks: list[dict] | None = None,
) -> list[tuple[str, str]]:
    """Parse delegations and add them to the task board.

    Deduplicates against existing open tasks by checking title similarity.
    Extracts priority from task text (e.g. [HIGH]: ...).

    Args:
        source_agent: Name of the agent whose response we're parsing.
        response: The LLM response text.
        agent_names: Set of valid agent names.
        add_task_fn: Callable(title, assigned_to, created_by) to create tasks.
        existing_tasks: Current open tasks for deduplication. If None, no dedup.

    Returns:
        List of (agent_name, task_desc) for delegations to unknown agents
        (hire requests). Empty list if all delegations were to known agents.
    """
    # Build set of existing task titles (lowered) for dedup
    existing_titles = set()
    if existing_tasks:
        for t in existing_tasks:
            if t.get("status") in ("todo", "in_progress"):
                existing_titles.add(t["title"].lower().strip())

    delegations = parse_delegations(response, agent_names)
    seen = set()  # Dedup within same response
    for target_agent, task_desc in delegations:
        if target_agent == source_agent:
            continue
        dedup_key = (target_agent, task_desc.lower().strip())
        if dedup_key in seen:
            logger.debug(f"Skipping duplicate delegation: {task_desc[:60]}")
            continue
        if _fuzzy_match(task_desc, existing_titles):
            logger.debug(f"Skipping similar task already on board: {task_desc[:60]}")
            continue
        seen.add(dedup_key)
        cleaned_desc, priority = _extract_priority(task_desc)
        # If priority extraction made the title too short, use the original
        if len(cleaned_desc.strip()) < 5:
            cleaned_desc = task_desc
        logger.info(f"Delegation: {source_agent} -> {target_agent} [{priority}]: {cleaned_desc[:80]}")
        title, details = _split_title_details(cleaned_desc)
        add_task_fn(title, assigned_to=target_agent, created_by=source_agent, priority=priority, details=details)

    # Find delegations to agents that don't exist — these are hire requests
    unknown = parse_unknown_delegations(response, agent_names)
    return unknown
