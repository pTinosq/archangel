import asyncio

from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from agent.reports import Report, Reporter


def test_a_report_is_a_page_and_a_tldr():
    def model(messages, info) -> ModelResponse:
        assert messages[-1].parts[-1].content == "Topic: hunting dogs\n\nMaterial:\nA long essay."
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"tldr": "Dachshunds.", "html": "<h1>"})])

    report = asyncio.run(Reporter(FunctionModel(model)).write("hunting dogs", "A long essay."))
    assert report == Report(tldr="Dachshunds.", html="<h1>")
