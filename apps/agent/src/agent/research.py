"""Answers from the web, never the phone: a quick search while the owner waits (Tavily: results the orchestrator
answers from, and pages it reads in full when a result only points to the answer), or research as a background task:
Perplexity's Pro Search via OpenRouter (several searches, many sources, under a minute, a few pence) or, when the owner
agrees to the cost, its Deep Research (~4 minutes, ~60p, an 80k-character essay whatever it's told).
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import activity, logs
from agent.messages import Image

RESEARCH = "perplexity/sonar-pro-search"
DEEP = "perplexity/sonar-deep-research"
DEEP_TIMEOUT = 900  # seconds: it researches for minutes before answering at all
TAVILY = "https://api.tavily.com"
PAGE_CHARACTERS = 30_000  # a long page's first part: where lists and answers usually are
IMAGE_BYTES = 4_000_000  # what a picture in a report may weigh
IMAGE_REDIRECTS = 4
LISTED_IMAGES = 8  # pictures a search or a page offers the reporter

Resolve = Callable[[str], Awaitable[list[str]]]


class Researcher:
    def __init__(
        self,
        model: Model,
        deep: Model,
        tavily: httpx.AsyncClient | None,
        pictures: httpx.AsyncClient | None = None,
        resolve: Resolve | None = None,
    ):
        """`tavily`: from `tavily(key)`; None, no quick search. `pictures`: fetches images from any address the
        model picked, so it carries no credentials; `resolve` is a test seam for DNS."""
        self._pictures = pictures or httpx.AsyncClient(timeout=20)
        self._resolve = resolve or _resolve
        self._research = Agent(model, name="research")
        self._deep = Agent(deep, name="deep research")
        self._tavily = tavily

    async def search(self, question: str) -> str:
        """Web results for `question`, in seconds."""
        if not self._tavily:
            return "Web search isn't set up (TAVILY_API_KEY)."
        with activity.call("tavily", "service", question) as reply:
            response = await self._tavily.post(f"{TAVILY}/search", json={"query": question})
            response.raise_for_status()
            results = response.json()["results"]
            reply(f"{len(results)} results")
        return "\n\n".join(f"{r['title']} ({r['url']})\n{r['content']}" for r in results) or "Nothing found."

    async def read(self, url: str) -> str:
        """The page at `url`, as text, in seconds."""
        if not self._tavily:
            return "Reading pages isn't set up (TAVILY_API_KEY)."
        with activity.call("tavily", "service", f"read {url}") as reply:
            response = await self._tavily.post(f"{TAVILY}/extract", json={"urls": [url]})
            response.raise_for_status()
            pages = response.json()["results"]
            reply(f"{len(pages[0]['raw_content'])} characters" if pages else "couldn't read it")
        return pages[0]["raw_content"][:PAGE_CHARACTERS] if pages else f"Couldn't read {url}."

    async def image_search(self, query: str) -> str:
        """Pictures for `query` from the web, one per line: address, then what it shows."""
        if not self._tavily:
            return "Image search isn't set up (TAVILY_API_KEY)."
        with activity.call("tavily", "service", f"images: {query}") as reply:
            response = await self._tavily.post(
                f"{TAVILY}/search",
                json={"query": query, "include_images": True, "include_image_descriptions": True, "max_results": 3},
            )
            response.raise_for_status()
            found = response.json().get("images", [])[:LISTED_IMAGES]
            reply(f"{len(found)} images")
        lines = [f"{i['url']} — {i.get('description') or 'no description'}" if isinstance(i, dict) else i for i in found]
        return "\n".join(lines) or "No images found."

    async def page_images(self, url: str) -> str:
        """The pictures on the page at `url`, one address per line."""
        if not self._tavily:
            return "Reading pages isn't set up (TAVILY_API_KEY)."
        with activity.call("tavily", "service", f"images on {url}") as reply:
            response = await self._tavily.post(f"{TAVILY}/extract", json={"urls": [url], "include_images": True})
            response.raise_for_status()
            pages = response.json()["results"]
            found = pages[0].get("images", [])[:LISTED_IMAGES] if pages else []
            reply(f"{len(found)} images")
        return "\n".join(found) or "No images on that page."

    async def image(self, url: str) -> Image | None:
        """The picture at `url`, or None if it can't be had: not public, not a picture, too big, gone."""
        with activity.call("web", "service", f"image {url}") as reply:
            try:
                found = await _fetch(self._pictures, self._resolve, url)
            except httpx.HTTPError:
                found = None
            reply(f"{len(found.data)} bytes" if found else "couldn't fetch it")
            return found

    async def research(self, question: str, deep: bool = False) -> str:
        """A cited answer, in under a minute; `deep`, an exhaustive one, in minutes."""
        result = await logs.run(self._deep if deep else self._research, question)
        # The text cites [1], [2]…; the sources come beside it, in order.
        citations = (result.response.provider_details or {}).get("annotations", [])
        urls = [c["url_citation"]["url"] for c in citations if c.get("type") == "url_citation"]
        sources = "\n".join(f"[{n}] {url}" for n, url in enumerate(urls, 1))
        return f"{result.output}\n\nSources:\n{sources}" if sources else result.output


def tavily(key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30)


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def _public(resolve: Resolve, url: str) -> bool:
    """Only web addresses that lead out to the internet: the model picks them from what the web said, so one may
    point at this machine or its neighbours (the relay, a cloud's metadata)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        return all(ipaddress.ip_address(a).is_global for a in await resolve(parts.hostname))
    except (OSError, ValueError):
        return False


def _kind(data: bytes) -> str | None:
    """A picture's type from its first bytes, never from what the server says (an SVG or a page isn't one)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _fetch(client: httpx.AsyncClient, resolve: Resolve, url: str) -> Image | None:
    for _ in range(IMAGE_REDIRECTS + 1):  # redirects followed by hand, each address checked
        if not await _public(resolve, url):
            return None
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code != 200:
                return None
            data = b""
            async for chunk in response.aiter_bytes():
                if len(data := data + chunk) > IMAGE_BYTES:
                    return None
        return Image(data, kind) if (kind := _kind(data)) else None
    return None
