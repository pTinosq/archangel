"""A second opinion when a phone task stops making progress.

A separate vision model looks at the recent screens and steps, says what's in the way and how to get past it.
It only runs when the pilot asks for one or runs out of steps, so everyday steps stay on the cheaper path.
"""

import logging
from typing import Protocol

from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import logs
from agent.messages import Image
from agent.models import Instructions, content

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
An agent operating an Android phone has stopped making progress on a task: it repeats actions, its actions \
fail, or the screen doesn't change. You get the task, its recent steps and the last few screenshots, oldest first.
Say what's in the way, judging by the screens (a dialog or keyboard covering things, a disabled control, \
a loading or frozen screen, the wrong app or page, a target described in a way that doesn't match the screen), \
and one concrete way past it. If the task can't be done from here, say so and why. Two or three sentences.\
"""


class Investigator(Protocol):
    async def investigate(self, goal: str, steps: list[str], screens: list[Image]) -> str | None:
        """What's blocking `goal` and how to get past it; None if no opinion could be had."""
        ...


class ModelInvestigator:
    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS):
        self._agent = Agent(model, name="investigator", instructions=instructions)

    async def investigate(self, goal: str, steps: list[str], screens: list[Image]) -> str | None:
        prompt = [
            f"Task: {goal}\nRecent steps, oldest first:\n" + "\n".join(f"- {step}" for step in steps),
            *(content(screen) for screen in screens),
        ]
        try:
            diagnosis = (await logs.run(self._agent, prompt)).output
        except Exception as error:
            # Advice is optional: without it the pilot still has its own judgement.
            log.warning("Investigator failed: %s", error)
            return None
        log.info("Investigator: %s", diagnosis)
        return diagnosis
