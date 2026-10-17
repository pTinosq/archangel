from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from agent.messages import Message

Deliver = Callable[[Message], Awaitable[None]]


class Channel(ABC):
    """A medium people talk to Archangel through: a terminal, a chat app, a phone line."""

    name: str

    @abstractmethod
    async def start(self, deliver: Deliver) -> None:
        """Receive messages, passing each to `deliver`, until the medium closes."""

    @abstractmethod
    async def send(self, message: Message) -> None:
        """Deliver a message from Archangel to `message.conversation`."""
