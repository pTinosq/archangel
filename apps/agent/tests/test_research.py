import asyncio
import json

import httpx
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from agent import activity
from agent.research import TAVILY, Researcher

ESSAY = FunctionModel(
    lambda messages, info: ModelResponse(
        parts=[TextPart("Tesco closes at 5pm.[1]")],
        provider_details={
            "annotations": [
                {"type": "url_citation", "url_citation": {"url": url, "title": ""}}
                for url in ("https://tesco.com/a", "https://b.uk")
            ]
        },
    )
)
RESEARCH = "Tesco closes at 5pm.[1]\n\nSources:\n[1] https://tesco.com/a\n[2] https://b.uk"


def test_research_cites_its_sources():
    assert asyncio.run(Researcher(ESSAY, ESSAY, None).research("When does Tesco close?")) == RESEARCH


def test_quick_searches_return_web_results():
    def tavily(request: httpx.Request) -> httpx.Response:
        assert (str(request.url), json.loads(request.content)) == (f"{TAVILY}/search", {"query": "Tesco hours"})
        return httpx.Response(200, json={"results": [{"title": "Tesco", "url": "https://tesco.com", "content": "8–5"}]})

    researcher = Researcher(ESSAY, ESSAY, httpx.AsyncClient(transport=httpx.MockTransport(tavily)))
    assert asyncio.run(researcher.search("Tesco hours")) == "Tesco (https://tesco.com)\n8–5"


def test_no_quick_search_without_a_key():
    assert asyncio.run(Researcher(ESSAY, ESSAY, None).search("Tesco hours")).startswith("Web search isn't set up")


def test_pages_a_search_points_to_are_read_in_full():
    page = "Letterboxd's Top 500 Films: 1. Harakiri"

    def tavily(request: httpx.Request) -> httpx.Response:
        assert (str(request.url), json.loads(request.content)) == (f"{TAVILY}/extract", {"urls": ["https://boxd.it/x"]})
        return httpx.Response(200, json={"results": [{"url": "https://boxd.it/x", "raw_content": page}]})

    researcher = Researcher(ESSAY, ESSAY, httpx.AsyncClient(transport=httpx.MockTransport(tavily)))
    assert asyncio.run(researcher.read("https://boxd.it/x")) == page
    calls = [(n.source, n.target, n.target_role) for n in activity._recent if n.kind == "service"]
    assert calls == [("archangel", "tavily", "service"), ("tavily", "archangel", "part")]  # in the cockpit
