import asyncio
import json

import httpx
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from agent.research import TAVILY, Report, Researcher

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


def writer(messages, info) -> ModelResponse:
    assert messages[-1].parts[-1].content == f"Asked: When does Tesco close?\n\n{RESEARCH}"
    page = {"summary": "5pm.", "html": "<h1>5pm</h1>"}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, page)])


def broken(messages, info) -> ModelResponse:
    raise RuntimeError("down")


def test_research_cites_its_sources_and_is_written_up_as_a_page():
    report = asyncio.run(Researcher(ESSAY, FunctionModel(writer), None).research("When does Tesco close?"))
    assert report == Report(RESEARCH, "5pm.", "<h1>5pm</h1>")


def test_without_a_page_the_whole_research_is_the_summary():
    report = asyncio.run(Researcher(ESSAY, FunctionModel(broken), None).research("When does Tesco close?"))
    assert report == Report(RESEARCH, RESEARCH, None)


def test_quick_searches_return_web_results():
    def tavily(request: httpx.Request) -> httpx.Response:
        assert (str(request.url), json.loads(request.content)) == (TAVILY, {"query": "Tesco hours"})
        return httpx.Response(200, json={"results": [{"title": "Tesco", "url": "https://tesco.com", "content": "8–5"}]})

    researcher = Researcher(ESSAY, ESSAY, httpx.AsyncClient(transport=httpx.MockTransport(tavily)))
    assert asyncio.run(researcher.search("Tesco hours")) == "Tesco (https://tesco.com)\n8–5"


def test_no_quick_search_without_a_key():
    assert asyncio.run(Researcher(ESSAY, ESSAY, None).search("Tesco hours")).startswith("Web search isn't set up")
