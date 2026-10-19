"""Stand-ins shared by the tests: channels and pilots that record what happens to them."""

import asyncio

import pydantic_ai

from agent.channels import Channel, Deliver
from agent.messages import Address, Image, Message, Text
from agent.reports import Report
from agent.runs import Outcome

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

    async def run(self, goal, record, paused=None, answer=None, inbox=None, said=None) -> Outcome:
        self.calls.append((goal, answer))
        self.inbox = inbox
        self.said = said
        record("Tapped the Post button.", SCREEN)
        if self.hold:
            await self.hold.wait()
        if self.outcome:
            return self.outcome
        if not paused:
            return Outcome("needs_approval", "Approve: post it?", run=pydantic_ai.Conversation(), screen=SCREEN)
        return Outcome("done" if answer.approved else "gave_up", f"Owner said {answer.text}.", screen=SCREEN)


class FakeResearcher:
    """Answers at once; `hold` keeps deep research going until set."""

    def __init__(self, hold: asyncio.Event | None = None):
        self.hold = hold

    async def search(self, question: str) -> str:
        return f"Quick answer to {question}."

    async def read(self, url: str) -> str:
        return f"The page at {url}."

    async def research(self, question: str, deep: bool = False) -> str:
        if self.hold:
            await self.hold.wait()
        return f"{'Everything' if deep else 'All'} about {question}."


class FakeReporter:
    async def write(self, topic: str, material: str) -> Report:
        return Report(tldr=f"{topic}: the gist.", title=topic, pages=[f"<p>{material}</p>"])


async def apps() -> dict[str, str]:
    return {"WhatsApp": "com.whatsapp", "Michelin Guide": "com.michelin"}


class FakeHelper(FakePilot):
    """To tasks, the helper is a pilot without the phone."""


class FakeConnectors:
    def __init__(self, described: str = ""):
        self.described = described

    def describe(self) -> str:
        return self.described
