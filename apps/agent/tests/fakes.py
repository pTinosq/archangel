"""Stand-ins shared by the tests: channels and pilots that record what happens to them."""

import asyncio

import pydantic_ai

from agent.channels import Channel, Deliver
from agent.messages import Address, Image, Message, Text
from agent.reports import Report
from agent.runs import Outcome
from agent.store import Store
from agent.threads import Threads
from agent.threads.router import Choice
from agent.threads.store import Thread, ThreadStore
from agent.threads.summariser import Summary

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
    def __init__(self):
        self.supplied: list = []

    async def write(self, topic: str, material: str, supplied=()) -> Report:
        self.supplied = list(supplied)
        return Report(tldr=f"{topic}: the gist.", title=topic, pages=[f"<p>{material}</p>"])


class FakeDevice:
    """A device as the orchestrator sees it: its apps, and whether it's there."""

    def __init__(self, label: str, apps: dict[str, str], state: str | None = "online"):
        self.label = label
        self._apps = apps
        self._state = state

    async def apps(self) -> dict[str, str]:
        return self._apps

    async def state(self) -> str | None:
        return self._state


def devices(pc: str | None = None) -> dict[str, FakeDevice]:
    """The phone, online, and a PC in state `pc` (None: there's never been one)."""
    return {
        "phone": FakeDevice("phone", {"WhatsApp": "com.whatsapp", "Michelin Guide": "com.michelin"}),
        "pc": FakeDevice("PC", {"Excel": "Microsoft.Office.EXCEL.EXE.15"}, pc),
    }


class FakeHelper(FakePilot):
    """To tasks, the helper is a pilot without the phone."""


class FakeConnectors:
    def __init__(self, described: str = ""):
        self.described = described

    def describe(self) -> str:
        return self.described


class FakeRouter:
    """Carries on the latest thread, as there was one conversation before threads; `choices`, in turn, if given."""

    def __init__(self, *choices: Choice):
        self.choices = list(choices)
        self.heard: list[tuple[str, list[int]]] = []

    async def choose(self, said: str, recent: list[Thread]) -> Choice:
        self.heard.append((said, [thread.id for thread in recent]))
        if self.choices:
            return self.choices.pop(0)
        return Choice(thread=recent[0].id) if recent else Choice(thread=None, title="Chat")


class FakeSummariser:
    def __init__(self):
        self.read: list[list[str]] = []

    async def summarise(self, thread: Thread, said: list[str], before: str = "") -> Summary:
        self.read.append([f"(before: {before})", *said] if before else said)
        return Summary(title=f"{thread.title} (summed up)", summary=f"{len(said)} lines about {thread.title}.")


def threads(store: Store, router: FakeRouter | None = None, summariser: FakeSummariser | None = None) -> Threads:
    """Real threads, in the store's file, with a router and summariser that need no model."""
    return Threads(ThreadStore(store.db), router or FakeRouter(), summariser or FakeSummariser())


class FakeCoder:
    """Claude Code on GitHub: every request comes back as PR #12."""

    def __init__(self):
        self.changes: list[tuple[str, str, int | None]] = []
        self.merged: list[int] = []

    async def change(self, request, branch, record, found=lambda run: None, run=None) -> tuple[bool, str]:
        """`run`: picked up after a restart, so nothing new starts."""
        self.changes.append((request, branch, run))
        if run is None:
            found(7)
            record("Claude Code is on it: https://github.com/runs/7")
        return True, f"https://github.com/pull/12 (tests pass)\n{request}"

    async def branch(self, pr: int) -> str:
        return f"archangel/pr-{pr}"

    async def merge(self, pr: int) -> str:
        self.merged.append(pr)
        return f"Merged PR #{pr}."
