import asyncio

from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from agent.reports import Report, Reporter


def test_a_report_is_pages_and_a_tldr():
    def model(messages, info) -> ModelResponse:
        assert messages[-1].parts[-1].content == "Topic: hunting dogs\n\nMaterial:\nA long essay."
        report = {"tldr": "Dachshunds.", "title": "Hunting dogs", "pages": ["<h1>Dachshunds</h1>"]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, report)])

    report = asyncio.run(Reporter(FunctionModel(model)).write("hunting dogs", "A long essay."))
    assert report == Report(tldr="Dachshunds.", title="Hunting dogs", pages=["<h1>Dachshunds</h1>"])


def test_pages_are_laid_out_as_numbered_phone_shaped_pages():
    html = Report(tldr="", title="Dogs & cats", pages=["<p>One</p>", "<p>Two</p>"]).html
    assert "<title>Dogs &amp; cats</title>" in html
    assert '<section class="page"><p>Two</p><footer>2 / 2</footer></section>' in html
    assert "aspect-ratio:9/16" in html
