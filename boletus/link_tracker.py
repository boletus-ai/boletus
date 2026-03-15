"""Track URLs created by agents — Notion pages, GitHub repos, Canva designs, etc."""

import json
import logging
import os
import re
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Domains we care about — things agents create via integrations
TRACKED_DOMAINS = (
    "notion.so", "notion.site",
    "github.com",
    "figma.com",
    "canva.com",
    "gamma.app",
    "vercel.app", "vercel.com",
    "stripe.com",
    "miro.com",
    "docs.google.com", "drive.google.com", "sheets.google.com",
    "linear.app",
    "posthog.com",
    "sentry.io",
    "cloudflare.com",
    "supabase.co",
)

# Regex to extract URLs from text
_URL_PATTERN = re.compile(
    r"https?://[^\s<>\"\'\)\]\}]+",
    re.IGNORECASE,
)


class LinkTracker:
    """Persists URLs that agents create or reference."""

    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.links_file = os.path.join(data_dir, "links.json")
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            with open(self.links_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, links: list[dict]):
        tmp = self.links_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(links, f, indent=2)
        os.replace(tmp, self.links_file)

    def extract_and_save(self, agent_name: str, text: str):
        """Extract tracked URLs from agent response and save them."""
        urls = _URL_PATTERN.findall(text)
        if not urls:
            return

        # Filter to tracked domains only
        new_links = []
        for url in urls:
            # Clean trailing punctuation
            url = url.rstrip(".,;:!?)")
            if any(domain in url.lower() for domain in TRACKED_DOMAINS):
                new_links.append(url)

        if not new_links:
            return

        with self._lock:
            links = self._load()
            existing_urls = {link["url"] for link in links}
            added = 0
            for url in new_links:
                if url not in existing_urls:
                    links.append({
                        "url": url,
                        "agent": agent_name,
                        "timestamp": datetime.now().isoformat(),
                    })
                    existing_urls.add(url)
                    added += 1
            if added:
                self._save(links)
                logger.info(f"Tracked {added} new link(s) from {agent_name}")

    def get_links(self) -> list[dict]:
        """Return all tracked links."""
        with self._lock:
            return self._load()

    def get_summary(self) -> str:
        """Human-readable summary for Slack."""
        links = self.get_links()
        if not links:
            return "No links tracked yet. Links appear here when agents create Notion pages, GitHub repos, Canva designs, etc."

        # Group by domain
        by_domain: dict[str, list[dict]] = {}
        for link in links:
            url = link["url"]
            # Extract domain
            domain = "other"
            for d in TRACKED_DOMAINS:
                if d in url.lower():
                    domain = d.split(".")[0].capitalize()
                    break
            by_domain.setdefault(domain, []).append(link)

        lines = []
        for domain, domain_links in sorted(by_domain.items()):
            lines.append(f"\n*{domain}:*")
            for link in domain_links:
                agent = link.get("agent", "unknown").upper()
                lines.append(f"  {link['url']}  ({agent})")

        return "Tracked links:\n" + "\n".join(lines)
