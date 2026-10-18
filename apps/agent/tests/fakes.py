"""Stand-ins shared by the tests: channels and pilots that record what happens to them."""

import asyncio

import pydantic_ai

from agent.channels import Channel, Deliver
from agent.messages import Address, Image, Message, Text
from agent.phone.pilot import Outcome

SCREEN = Image(b"jpg", "image/jpeg")


class FakeChannel(Channel):
    def __init__(self, name: str, inbound: list[str] = ()):
        self.name = name
        self.owner = Address(name, "owner")
        self.inbound = inbound
        self.sent: list[Message] = []

    async def start(self, deliver: Deliver) -> None:
        for text in self.inbound:
            await deliver(Message(self.owner, (Text(text),)))

    async def send(self, message: Message) -> None:
        self.sent.append(message)


class FakePilot:
    """Records a step, then returns `outcome`; by default asks for approval first and does what the owner said."""

    def __init__(self, outcome: Outcome | None = None, hold: asyncio.Event | None = None):
        self.outcome = outcome
        self.hold = hold
        self.calls: list[tuple[str, object]] = []

    async def run(self, goal, record, paused=None, answer=None, inbox=None) -> Outcome:
        self.calls.append((goal, answer))
        self.inbox = inbox
        record("Tapped the Post button.", SCREEN)
        if self.hold:
            await self.hold.wait()
        if self.outcome:
            return self.outcome
        if not paused:
            return Outcome("needs_approval", "Approve: post it?", run=pydantic_ai.Conversation(), screen=SCREEN)
        return Outcome("done" if answer.approved else "gave_up", f"Owner said {answer.text}.", screen=SCREEN)
