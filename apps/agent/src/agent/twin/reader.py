"""The twin's reader: walks the graph for another agent and says only what bears on its task. Read-only."""

import logging
from datetime import date

from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import activity, logs
from agent.models import Instructions
from agent.twin.graph import ROOT, Graph

log = logging.getLogger(__name__)

INSTRUCTIONS = """You read the owner's context twin for another agent: a graph of what's lasting about them. Each \
node is a markdown page; each link has a few-word preview of what's at its end. Open only what the task needs, \
following previews: usually two or three nodes. Then answer with only what bears on it, in a few short lines: \
concrete (names, addresses, rules, limits), nothing they'd have to second-guess, no padding. Nothing bears on it? \
"Nothing relevant." """


class Reader:
    def __init__(self, model: Model, graph: Graph, instructions: Instructions = INSTRUCTIONS):
        self._graph = graph

        async def open(name: str) -> str:  # async: sync tools run on a thread, and SQLite belongs to this one
            """A node's page and its links, by name or alias."""
            activity.hop("twin", "tool", name)  # the Context twin tab lights the node up
            try:
                return graph.open(name)
            except ValueError as error:
                return f"{error} Nodes: {', '.join(n.name for n in graph.nodes())}."

        self._agent = Agent(
            model,
            name="twin reader",
            instructions=[instructions, lambda: f"Today is {date.today():%A %d %B %Y}."],
            tools=[open],
        )

    async def brief(self, goal: str) -> str:
        """What an agent about to do `goal` should know about the owner; "" if nothing."""
        return await self._read(f"An agent is about to do this for the owner: {goal}\nWhat should it know?")

    async def ask(self, question: str) -> str:
        """An agent's question about the owner, mid-task."""
        return await self._read(f"An agent working for the owner asks: {question}") or "Nothing relevant."

    async def _read(self, prompt: str) -> str:
        # The root comes with the question: every walk starts there, so it costs no call.
        result = await logs.run(self._agent, f"{prompt}\n\nYou're at the root:\n{self._graph.open(ROOT)}")
        found = result.output.strip()
        return "" if found.rstrip(".").casefold() == "nothing relevant" else found
