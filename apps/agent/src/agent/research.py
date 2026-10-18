"""Answers from the web, never the phone: a quick search while the owner waits (Tavily: results the orchestrator
answers from), or research (Perplexity's Pro Search via OpenRouter: several searches, many sources, under a minute,
a few pence; its Deep Research was ~20x the price for an 80k-character essay) as a background task.
"""

import httpx
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import logs

RESEARCH = "perplexity/sonar-pro-search"
TAVILY = "https://api.tavily.com/search"


class Researcher:
    def __init__(self, model: Model, tavily: httpx.AsyncClient | None):
        """`tavily`: from `tavily(key)`; None, no quick search."""
        self._research = Agent(model, name="research")
        self._tavily = tavily

    async def search(self, question: str) -> str:
        """Web results for `question`, in seconds."""
        if not self._tavily:
            return "Web search isn't set up (TAVILY_API_KEY)."
        response = await self._tavily.post(TAVILY, json={"query": question})
        response.raise_for_status()
        results = response.json()["results"]
        return "\n\n".join(f"{r['title']} ({r['url']})\n{r['content']}" for r in results) or "Nothing found."

    async def research(self, question: str) -> str:
        """A cited answer, in under a minute."""
        result = await logs.run(self._research, question)
        # The text cites [1], [2]…; the sources come beside it, in order.
        citations = (result.response.provider_details or {}).get("annotations", [])
        urls = [c["url_citation"]["url"] for c in citations if c.get("type") == "url_citation"]
        sources = "\n".join(f"[{n}] {url}" for n, url in enumerate(urls, 1))
        return f"{result.output}\n\nSources:\n{sources}" if sources else result.output


def tavily(key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30)
