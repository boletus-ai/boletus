"""Convert GitHub-flavored markdown to Slack mrkdwn format."""

import re


def markdown_to_slack(text: str) -> str:
    """Convert GitHub-flavored markdown to Slack's mrkdwn format.

    Slack mrkdwn differences from GitHub markdown:
    - Bold: *text* (single asterisk), not **text**
    - No heading syntax (## is not supported)
    - No table syntax (| col | col |)
    - Links: <url|text>, not [text](url)
    - Code blocks: ```code``` (same)
    - No image rendering
    """
    # Split into code blocks and non-code blocks to avoid converting inside code
    parts = re.split(r'(```[\s\S]*?```)', text)
    converted = []

    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Inside a code block — leave unchanged
            converted.append(part)
        else:
            converted.append(_convert_segment(part))

    return "".join(converted)


def _convert_segment(text: str) -> str:
    """Convert a non-code segment from markdown to Slack mrkdwn."""
    lines = text.split("\n")
    result = []
    table_rows: list[list[str]] = []
    in_table = False

    for line in lines:
        stripped = line.strip()

        # Detect end of table
        if in_table and not stripped.startswith("|"):
            result.extend(_format_table(table_rows))
            table_rows = []
            in_table = False

        # Table separator row (|---|---|) — skip
        if re.match(r"^\s*\|[\s\-:|\u2014]+\|?\s*$", stripped):
            in_table = True
            continue

        # Table data row
        if stripped.startswith("|") and "|" in stripped[1:]:
            in_table = True
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            table_rows.append(cells)
            continue

        # Headers: ## Text → *Text*
        header_match = re.match(r"^(#{1,6})\s+(.*)", line)
        if header_match:
            heading_text = header_match.group(2).strip()
            # Remove trailing # (some markdown styles)
            heading_text = re.sub(r"\s*#+\s*$", "", heading_text)
            result.append(f"\n*{heading_text}*")
            continue

        # Horizontal rules: --- or *** or ___
        if re.match(r"^\s*[-*_]{3,}\s*$", stripped):
            result.append("───")
            continue

        result.append(line)

    # Flush remaining table
    if table_rows:
        result.extend(_format_table(table_rows))

    text = "\n".join(result)

    # Bold: **text** → *text* (do this before single * handling)
    # Handle both **text** and __text__ for bold
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"_\1_", text)

    # Images: ![alt](url) → just the alt text
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)

    # Links: [text](url) → <url|text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)

    # Clean up excessive blank lines (max 2 in a row)
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    return text


def _format_table(rows: list[list[str]]) -> list[str]:
    """Convert markdown table rows to Slack-readable text."""
    if not rows:
        return []

    result = []

    # If first row looks like a header, bold it
    if rows:
        header = rows[0]
        header_line = " │ ".join(f"*{c}*" for c in header if c.strip())
        if header_line.strip():
            result.append(header_line)

        for row in rows[1:]:
            line = " │ ".join(c for c in row if c.strip())
            if line.strip():
                result.append(line)

    return result
