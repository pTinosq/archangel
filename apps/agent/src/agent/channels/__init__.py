from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from agent.messages import Address, Message

Deliver = Callable[[Message], Awaitable[None]]


class Channel(ABC):
    """A medium the owner talks to Archangel through: a terminal, a chat app, email, a phone line.

    All channels feed one conversation. A channel only moves messages; what to say, and where, is decided upstream.
    """

    name: str
    owner: Address
    """Where the owner is reached on this channel, for messages Archangel starts rather than answers."""

    @abstractmethod
    async def start(self, deliver: Deliver) -> None:
        """Receive the owner's messages, passing each to `deliver`, until the medium closes."""

    @abstractmethod
    async def send(self, message: Message) -> None:
        """Deliver a message from Archangel to `message.address`."""
