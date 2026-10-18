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


def test_a_report_is_a_cover_then_numbered_phone_shaped_pages():
    html = Report(tldr="Both <3", title="Dogs & cats", pages=["<p>One</p>", "<p>Two</p>"]).html
    assert "<title>Dogs &amp; cats</title>" in html
    assert "<h1>Dogs &amp; cats</h1>" in html and "<p>Both &lt;3</p>" in html  # the cover
    assert "<p>Two</p><footer><span>Archangel</span><span>3 / 3</span></footer>" in html
    assert "aspect-ratio:9/16" in html
