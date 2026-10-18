"""The reporter: turns material about a topic (research, so far) into a short web page to read on
a phone, and a tldr for the orchestrator to pass on with its link."""

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import logs
from agent.models import Instructions

INSTRUCTIONS = """Write a report on the topic from the material, as a web page to read on a phone, for a busy person. \
Shorter and clearer: the answer first, then what backs it up, in short sections, tables where they help, sources as \
links. Keep what matters; add nothing. One HTML file, CSS inline, light and dark, no scripts or outside files."""


class Report(BaseModel):
    tldr: str = Field(description="The answer in a sentence or two, to text the owner with the link.")
    html: str


class Reporter:
    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS):
        self._agent = Agent(model, name="reporter", instructions=instructions, output_type=Report)

    async def write(self, topic: str, material: str) -> Report:
        return (await logs.run(self._agent, f"Topic: {topic}\n\nMaterial:\n{material}")).output
