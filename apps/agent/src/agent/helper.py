"""The sub-agent for the owner's online services: does one task with the connectors' tools (Gmail, Calendar, Notion,
Slack…), never the phone. Pauses, resumably, for the owner's approval of anything that isn't read-only, or to ask.
"""

import logging

import pydantic_ai
from pydantic_ai import Agent, CallDeferred, DeferredToolRequests, RunContext
from pydantic_ai.models import Model

from agent import logs
from agent.connectors import Connectors
from agent.models import Instructions
from agent.runs import Answer, Outcome, Record, answered, question, results

log = logging.getLogger(__name__)

INSTRUCTIONS = """Do the owner's task in their online services, with the tools you have, in as few calls as it \
takes. Anything that isn't read-only waits for their OK by itself: just call it. Only ask_owner what only they \
know. End with what you found or did: complete, but short enough to text."""


class Helper:
    def __init__(self, model: Model, connectors: Connectors, instructions: Instructions = INSTRUCTIONS):
        self._connectors = connectors
        self._agent = Agent(
            model,
            name="helper",
            instructions=[instructions, self._connected],
            output_type=[str, DeferredToolRequests],
            tools=[ask_owner],
        )

    async def run(
        self, goal: str, record: Record, paused: pydantic_ai.Conversation | None = None, answer: Answer | None = None
    ) -> Outcome:
        """Do `goal`, or continue a `paused` run with the owner's `answer`. Each call to a service goes to `record`."""
        if not self._connectors.connected():
            return Outcome("failed", "Nothing's connected: add services on the panel's Connectors page.")
        record(f"Continuing. {answered(answer)}" if paused else "Started.", None)
        resume = {"conversation": paused, "deferred_tool_results": results(paused, answer)} if paused else {}
        try:
            result = await logs.run(
                self._agent,
                None if paused else goal,
                toolsets=self._connectors.toolsets(lambda call: record(call, None)),
                **resume,
            )
        except Exception as error:  # a service or the model: the orchestrator decides whether to retry
            log.exception("Connectors task failed")
            return Outcome("failed", f"Failed ({type(error).__name__}: {error}).")
        if isinstance(output := result.output, DeferredToolRequests):
            status = "needs_approval" if output.approvals else "needs_answer"
            return Outcome(status, question(output), result.conversation)
        return Outcome("done", output)

    async def _connected(self) -> str:  # async: sync ones run on a thread, away from SQLite
        return f"Connected, by profile: {self._connectors.describe(prefixes=True)}. Use the profile the task means."


async def ask_owner(ctx: RunContext, question: str) -> str:
    """Ask the owner something only they can answer, e.g. which of two Sams. The task pauses until they reply."""
    raise CallDeferred(metadata={"question": question})
