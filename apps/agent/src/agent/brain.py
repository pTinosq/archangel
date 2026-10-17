from pathlib import Path

from pydantic_ai import Agent, BinaryContent, UserContent
from pydantic_ai.models import Model

from agent.gateway import Reply
from agent.messages import Message, Text
from agent.threads import Threads

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()


class Brain:
    """Turns each incoming message into a model run over that conversation's history."""

    def __init__(self, model: Model, threads: Threads):
        self._agent = Agent(model, instructions=INSTRUCTIONS)
        self._threads = threads

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
