from pathlib import Path

from pydantic_ai import Agent, BinaryContent, UserContent
from pydantic_ai.models import Model

from agent.gateway import Reply
from agent.messages import Message, Text
from agent.phone.pilot import Pilot
from agent.threads import Threads

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()


class Brain:
    """Turns each incoming message into a model run over that conversation's history."""

    def __init__(self, model: Model, threads: Threads, pilot: Pilot):
        self._agent = Agent(model, instructions=INSTRUCTIONS)
        self._threads = threads

        @self._agent.tool_plain
        async def use_phone(task: str) -> str:
            """Do something on the owner's Android phone and report back.

            Describe the whole task and what to find out, e.g. "Open WhatsApp and read the latest
            message from Alex". Returns what was done and seen.
            """
            return await pilot.run(task)

    async def handle(self, message: Message, reply: Reply) -> None:
        history = self._threads.history(message.conversation)
        result = await self._agent.run(_prompt(message), message_history=history)
        self._threads.save(message.conversation, result.all_messages())
        await reply(Message(message.conversation, (Text(result.output),)))


def _prompt(message: Message) -> list[UserContent]:
    return [
        part.text if isinstance(part, Text) else BinaryContent(data=part.data, media_type=part.media_type)
        for part in message.parts
    ]
