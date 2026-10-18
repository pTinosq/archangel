"""Answers from the web, never the phone: a quick search while the owner waits (Tavily: results the orchestrator
answers from), or deep research (Perplexity Sonar Deep Research via OpenRouter: many sources, read and cross-checked,
minutes long) as a background task. Deep research writes long essays, so a second model makes a short web page of
each, and a summary to pass on.
"""

import logging
from dataclasses import dataclass

import httpx
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import logs
from agent.models import Instructions

log = logging.getLogger(__name__)

DEEP = "perplexity/sonar-deep-research"
TAVILY = "https://api.tavily.com/search"
INSTRUCTIONS = """Turn this research into a web page to read on a phone, for a busy person. Shorter and clearer: the \
answer first, then what backs it up, in short sections, tables where they help, sources as links. Keep what matters; \
add nothing. One HTML file, CSS inline, light and dark, no scripts or outside files. And a summary: the answer in a \
few sentences, to text them."""


@dataclass(frozen=True)
class Report:
    research: str
    """All of it, with its sources."""
    summary: str
    """What to tell the owner: a few sentences, or all of it if the page couldn't be written."""
    page: str | None
    """A web page of it; None if writing one failed."""


@dataclass(frozen=True)
class _Page:
    summary: str
    html: str


class Researcher:
    def __init__(
        self, deep: Model, writer: Model, tavily: httpx.AsyncClient | None, instructions: Instructions = INSTRUCTIONS
    ):
        """`writer` makes the page; `tavily`: from `tavily(key)`; None, no quick search."""
        self._deep = Agent(deep, name="research")
        self._writer = Agent(writer, name="writer", instructions=instructions, output_type=_Page)
        self._tavily = tavily

    async def search(self, question: str) -> str:
        """Web results for `question`, in seconds."""
        if not self._tavily:
            return "Web search isn't set up (TAVILY_API_KEY)."
        response = await self._tavily.post(TAVILY, json={"query": question})
        response.raise_for_status()
        results = response.json()["results"]
        return "\n\n".join(f"{r['title']} ({r['url']})\n{r['content']}" for r in results) or "Nothing found."

    async def research(self, question: str) -> Report:
        """A cited report, in minutes."""
        result = await logs.run(self._deep, question)
        # The text cites [1], [2]…; the sources come beside it, in order.
        citations = (result.response.provider_details or {}).get("annotations", [])
        urls = [c["url_citation"]["url"] for c in citations if c.get("type") == "url_citation"]
        sources = "\n".join(f"[{n}] {url}" for n, url in enumerate(urls, 1))
        text = f"{result.output}\n\nSources:\n{sources}" if sources else result.output
        try:
            page = (await logs.run(self._writer, f"Asked: {question}\n\n{text}")).output
        except Exception:  # the research is what matters; the page is a nicety
            log.exception("Couldn't write the report's page")
            return Report(text, text, None)
        return Report(text, page.summary, page.html)


def tavily(key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30)
