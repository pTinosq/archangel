import logging
from pathlib import Path

from pydantic_ai import Agent, BinaryContent, UserContent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models import Model

from agent import history, logs
from agent.gateway import Reply
from agent.messages import Message, Text
from agent.phone.pilot import Pilot
from agent.store import Store

log = logging.getLogger(__name__)

TOOL_RETRIES = 3
INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()


class Brain:
    """Turns each incoming message into a model run over that conversation's history."""

    def __init__(self, model: Model, store: Store, pilot: Pilot):
        self._agent = Agent(
            model,
            instructions=INSTRUCTIONS,
            retries=TOOL_RETRIES,
            capabilities=[ProcessHistory(history.recent), ProcessHistory(history.forget_old_screenshots)],
        )
        self._store = store

        @self._agent.tool_plain
        async def use_phone(task: str) -> str:
            """Do something on the owner's Android phone and report back.

            Describe the whole task and what to find out, e.g. "Open WhatsApp and read the latest
            message from Alex". Returns what was done and seen.
            """
            return (await pilot.run(task)).report

    async def handle(self, message: Message, reply: Reply) -> None:
        messages = self._store.history(message.conversation)
        log.debug("in %s (%d prior messages): %s", message.conversation, len(messages), message)
        result = await logs.run(self._agent, _prompt(message), message_history=messages)
        self._store.save_history(message.conversation, result.all_messages())
        log.debug("out %s: %s", message.conversation, result.output)
        await reply(Message(message.conversation, (Text(result.output),)))


def _prompt(message: Message) -> list[UserContent]:
    return [
        part.text if isinstance(part, Text) else BinaryContent(data=part.data, media_type=part.media_type)
        for part in message.parts
    ]
