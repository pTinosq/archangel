import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Protocol

from agent.channels import Channel
from agent.messages import Conversation, Message, Text

log = logging.getLogger(__name__)

Reply = Callable[[Message], Awaitable[None]]


class Brain(Protocol):
    async def handle(self, message: Message, reply: Reply) -> None: ...


class Gateway:
    """Connects channels to the brain.

    Delivery never blocks a channel. Messages in one conversation are handled in order, one at a time;
    different conversations run concurrently. The brain may reply any number of times.
    """

    def __init__(self, channels: list[Channel], brain: Brain):
        self._channels = {channel.name: channel for channel in channels}
        self._brain = brain
        self._locks: defaultdict[Conversation, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._running: set[asyncio.Task] = set()

    async def run(self) -> None:
        """Run until every channel closes, then finish the messages already received."""
        async with asyncio.TaskGroup() as group:
            for channel in self._channels.values():
                group.create_task(channel.start(self._deliver))
        await asyncio.gather(*self._running)

    async def _deliver(self, message: Message) -> None:
        task = asyncio.create_task(self._handle(message))
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def _handle(self, message: Message) -> None:
        async with self._locks[message.conversation]:
            try:
                await self._brain.handle(message, self._send)
            except Exception:
                log.exception("Failed to handle message in %s", message.conversation)
                await self._send(Message(message.conversation, (Text("Something went wrong. Try again."),)))

    async def _send(self, message: Message) -> None:
        await self._channels[message.conversation.channel].send(message)
