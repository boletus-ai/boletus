"""Download and extract text from files uploaded to Slack."""

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Max file size to process (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024

# Supported extensions and their handlers
SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".pdf", ".docx", ".doc", ".rtf"}


def download_slack_file(url: str, bot_token: str) -> bytes | None:
    """Download a file from Slack's private URL.

    Args:
        url: The ``url_private_download`` from a Slack file object.
        bot_token: Slack bot token for authorization.

    Returns:
        Raw file bytes, or None on failure.
    """
    try:
        import requests
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {bot_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            logger.error(f"Slack returned HTML instead of file — check files:read scope. URL: {url[:80]}")
            return None
        if len(resp.content) > MAX_FILE_SIZE:
            logger.warning(f"File too large ({len(resp.content)} bytes), skipping")
            return None
        return resp.content
    except Exception as exc:
        logger.error(f"Failed to download Slack file: {exc}")
        return None


def extract_text(data: bytes, filename: str) -> str:
    """Extract text content from file bytes based on file extension.

    Supports:
    - Plain text: .txt, .md, .csv, .json, .yaml, .yml
    - PDF: .pdf (requires ``pypdf``)
    - Word: .docx (requires ``python-docx``)

    Args:
        data: Raw file bytes.
        filename: Original filename (used to detect format).

    Returns:
        Extracted text, or empty string on failure.
    """
    ext = Path(filename).suffix.lower()

    if ext in (".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".rtf"):
        return _extract_plain(data)

    if ext == ".pdf":
        return _extract_pdf(data)

    if ext in (".docx", ".doc"):
        return _extract_docx(data)

    logger.warning(f"Unsupported file type: {ext}")
    return ""


def save_to_context_dir(context_dir: str, filename: str, content: str) -> str:
    """Save extracted text to the context directory for agents.

    Args:
        context_dir: Path to the context/ directory.
        filename: Original filename (sanitized for filesystem).
        content: Extracted text content.

    Returns:
        Path to the saved file.
    """
    os.makedirs(context_dir, exist_ok=True)

    # Sanitize filename — keep base name, change extension to .md
    base = Path(filename).stem
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in base)
    safe_name = safe_name.strip("-")[:80] or "uploaded-doc"
    target = os.path.join(context_dir, f"{safe_name}.md")

    # Avoid overwriting
    counter = 1
    while os.path.exists(target):
        target = os.path.join(context_dir, f"{safe_name}-{counter}.md")
        counter += 1

    with open(target, "w", encoding="utf-8") as f:
        f.write(f"# {filename}\n\n{content}")

    logger.info(f"Saved uploaded file to {target}")
    return target


def process_slack_files(
    files: list[dict],
    bot_token: str,
    context_dir: str = "",
) -> list[tuple[str, str]]:
    """Process a list of Slack file objects: download, extract, optionally save.

    Args:
        files: List of Slack file dicts (from message event ``files`` field).
        bot_token: Slack bot token.
        context_dir: If set, save extracted text here for agents.

    Returns:
        List of ``(filename, extracted_text)`` tuples for successfully parsed files.
    """
    results = []

    for file_obj in files:
        filename = file_obj.get("name", "unknown")
        ext = Path(filename).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            logger.info(f"Skipping unsupported file: {filename}")
            continue

        url = file_obj.get("url_private_download", "")
        if not url:
            logger.warning(f"No download URL for file: {filename}")
            continue

        data = download_slack_file(url, bot_token)
        if not data:
            continue

        text = extract_text(data, filename)
        if not text.strip():
            logger.warning(f"No text extracted from: {filename}")
            continue

        # Truncate very long documents for the wizard prompt
        if len(text) > 50000:
            text = text[:50000] + "\n\n... [truncated — full document saved to context/]"

        if context_dir:
            save_to_context_dir(context_dir, filename, text)

        results.append((filename, text))
        logger.info(f"Extracted {len(text)} chars from {filename}")

    return results


# ---------------------------------------------------------------------------
# Format-specific extractors
# ---------------------------------------------------------------------------

def _extract_plain(data: bytes) -> str:
    """Extract text from plain text formats."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    """Extract text from PDF using pypdf."""
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except Exception as exc:
        logger.error(f"PDF extraction failed: {exc}")
        return ""


def _extract_docx(data: bytes) -> str:
    """Extract text from DOCX using python-docx."""
    try:
        import io
        from docx import Document
        doc = Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except Exception as exc:
        logger.error(f"DOCX extraction failed: {exc}")
        return ""
