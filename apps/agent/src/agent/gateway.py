"""Where every channel meets the orchestrator, both ways.

In: each owner message becomes an event, handled concurrently so no channel waits on another.
Out: each message goes to the channel it's addressed to. Both directions are recorded in one transcript.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from agent import activity
from agent.channels import Channel
from agent.events import Event, OwnerMessage
from agent.messages import Address, Message
from agent.store import Store

log = logging.getLogger(__name__)

Handle = Callable[[Event], Awaitable[None]]


class Gateway:
    def __init__(self, channels: list[Channel], store: Store):
        self._channels = {channel.name: channel for channel in channels}
        for name in self._channels:
            activity.declare(name, "channel")
        self._store = store
        self._running: set[asyncio.Task] = set()

    @property
    def channels(self) -> list[str]:
        return list(self._channels)

    def describe(self) -> str:
        """Each channel and what it can show, e.g. "panel (text and pictures), voice (speech only…)"."""
        return ", ".join(f"{name} ({channel.shows})" for name, channel in self._channels.items())

    def owner(self, channel: str) -> Address | None:
        """The owner's address on `channel`, if Archangel has that channel."""
        found = self._channels.get(channel)
        return found and found.owner

    async def run(self, handle: Handle) -> None:
        """Run until every channel closes, then finish the messages already received."""

        async def deliver(message: Message | Event) -> None:
            if isinstance(message, Message):
                self._store.add_line(message, "owner")
                handling = self._handle(handle, message)
            else:  # a notice: no one said it, so it isn't in the transcript
                handling = handle(message)
            task = asyncio.create_task(handling, context=activity.new_request("gateway"))
            self._running.add(task)
            task.add_done_callback(self._running.discard)

        async with asyncio.TaskGroup() as group:
            for channel in self._channels.values():
                group.create_task(channel.start(deliver))
        await asyncio.gather(*self._running)

    async def send(self, message: Message) -> None:
        if not message.text.strip() and not message.images:  # a model with nothing to say; Twilio rejects empties
            return
        self._store.add_line(message, "archangel")
        activity.hop("gateway", "message", message.text)
        activity.hop(message.address.channel, "message", message.text, source="gateway")
        if not (channel := self._channels.get(message.address.channel)):
            log.warning("No channel %r; dropped: %s", message.address.channel, message.text)
            return
        try:
            await channel.send(message)
        except Exception:
            # One medium failing (network, rate limits) mustn't take down the conversation; it's in the transcript.
            log.exception("Couldn't send via %s", channel.name)

    async def _handle(self, handle: Handle, message: Message) -> None:
        activity.hop("gateway", "message", message.text, source=message.address.channel)
        activity.hop("orchestrator", "message", message.text)
        await handle(OwnerMessage(message))
