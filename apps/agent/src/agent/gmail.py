"""Gmail messages as the helper reads them (workspace-mcp's tools, tidied on the way back).

A shop's email hides what matters behind HTML buttons: its plain-text part often has no links, and its HTML can be
mostly inline base64 images, so workspace-mcp's 20,000-character cut lands before the button. So each message's
links are fetched from its whole HTML and listed after it, and inline images are cut out of what's shown.
"""

import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONTENT = {
    "get_gmail_message_content",
    "get_gmail_messages_content_batch",
    "get_gmail_thread_content",
    "get_gmail_threads_content_batch",
}
MOST_LINKS = 100  # a newsletter's footer can have hundreds, each a long tracking URL
DATA_URI = re.compile(r"data:[\w.+/-]*(?:;[\w.+=-]+)*,[A-Za-z0-9+/=%_.~-]*")
SAVED = re.compile(r"Saved to: (.+)")


def processor(files: Path):
    """MCPToolset's `process_tool_call` hook. `files`: where workspace-mcp saves what it exports
    (`WORKSPACE_ATTACHMENT_DIR`), read back from here and deleted."""

    async def process(ctx, call_tool, name: str, args: dict[str, Any]) -> Any:
        result = await call_tool(name, args)
        if name not in CONTENT or not isinstance(result, str):
            return result
        html = result
        if name == "get_gmail_message_content" and args.get("format", "full") == "full" and not args.get("full"):
            html = await _whole_html(call_tool, name, args, files) or result
        return tidy(result, html)

    return process


def tidy(result: str, html: str) -> str:
    """`result` without inline images, and the links in `html` after it."""
    result = DATA_URI.sub("data:[inline image]", result)
    if not (found := links(html)):
        return result
    shown = [f"{text} → {url}" if text else url for url, text in found[:MOST_LINKS]]
    if len(found) > MOST_LINKS:
        shown.append(f"(and {len(found) - MOST_LINKS} more)")
    return result + "\n\n--- LINKS (from the HTML) ---\n" + "\n".join(shown)


def links(html: str) -> list[tuple[str, str]]:
    """Each link once, with its text (or its image's alt text): (url, text)."""
    parser = _Links()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # odd HTML: whatever was found so far
        log.debug("Couldn't read all of an email's HTML", exc_info=True)
    return list(parser.found.items())


async def _whole_html(call_tool, name: str, args: dict[str, Any], files: Path) -> str | None:
    """The message's whole HTML: workspace-mcp saves it to a file rather than cut it."""
    try:
        saved = await call_tool(name, {**args, "full": True, "body_format": "html"})
        if not isinstance(saved, str) or not (match := SAVED.search(saved)):
            return None
        path = Path(match.group(1).strip()).resolve()
        if not path.is_relative_to(files.resolve()):  # only ever read what it saved for us
            return None
        try:
            return path.read_text(errors="replace")
        finally:
            path.unlink(missing_ok=True)
    except Exception:
        log.warning("Couldn't fetch an email's whole HTML for its links", exc_info=True)
        return None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found: dict[str, str] = {}
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self._close()
            href = (attrs.get("href") or "").strip()
            self._href = href if href.startswith(("http://", "https://", "mailto:")) else None
            self._text = []
        elif tag == "img" and self._href and attrs.get("alt"):
            self._text.append(attrs["alt"])

    def handle_data(self, data):
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self._close()

    def close(self):
        super().close()
        self._close()

    def _close(self):
        if self._href:
            text = " ".join(" ".join(self._text).split())
            if not self.found.get(self._href):  # first seen, unless only now with words
                self.found[self._href] = text
        self._href = None
