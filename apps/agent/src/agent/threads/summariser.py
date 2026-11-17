"""The summariser: a finished thread in, a short summary and a title out, so the router can tell what an old thread
was about without reading it, and the owner's "how much were they again?" is answered from it."""

from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import logs
from agent.models import Instructions
from agent.threads.store import Thread

INSTRUCTIONS = """You summarise a finished conversation between the owner and Archangel, their assistant, for later: \
what they asked, what was found or done (keep the concrete facts: prices, names, times, links, task numbers), and \
anything left open. A few short lines, plain text. And a title of a few words."""

READ = 60_000
"""Characters of a thread the summariser reads, from its end: a long one's start is in the summary it carries on from,
or long settled."""


class Summary(BaseModel):
    title: str = Field(description="A few words")
    summary: str = Field(description="A few short lines")


class Summariser:
    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS):
        self._agent = Agent(
            model,
            name="thread summariser",
            output_type=Summary,
            instructions=[instructions, lambda: f"Today is {date.today():%A %d %B %Y}."],
        )

    async def summarise(self, thread: Thread, said: list[str], before: str = "") -> Summary:
        """`before`: the summary of the thread this one carries on from, so this summary stands alone."""
        conversation = "\n".join(said) or "(nothing was said)"
        if len(conversation) > READ:
            conversation = "(earlier parts left out)\n…" + conversation[-READ:]
        carried = f"It carries on from one that went: {before}\n" if before else ""
        result = await logs.run(self._agent, f'Thread "{thread.title}":\n{carried}{conversation}')
        return result.output
