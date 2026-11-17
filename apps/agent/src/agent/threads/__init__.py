"""Threads (M18, `docs/threads.md`): one conversation with the owner, kept as many threads, one per topic, so the
orchestrator answers each message with its thread's history rather than the last of everything.

`Threads.route(event)` says which thread an event belongs in: the router (a quick model) chooses for what the owner
says; a task's update goes to the thread that started it; anything else to the latest. `history` and `save` keep each
thread's history. A thread the owner wraps up ("thanks") closes and is summarised; so is one left quiet. A full one
(its context past `FULL` tokens) is summarised and carried on in a new one, which starts from that summary.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from pydantic_ai import ModelMessage, ModelResponse
from pydantic_ai.toolsets import FunctionToolset

from agent import activity
from agent.events import Event, Notice, OwnerMessage, TaskUpdate
from agent.threads.router import Router, search
from agent.threads.store import Thread, ThreadStore
from agent.threads.summariser import Summariser
from agent.threads.text import ago, lines

log = logging.getLogger(__name__)

FULL = 30_000
"""Tokens of context past which a thread is carried on in a new one: well inside the model's window, and cheap."""
QUIET = timedelta(hours=3)
"""An open thread nothing's been said in for this long is finished: summarised and closed."""
CHECK = timedelta(minutes=1)
RECENT = 10
"""Threads the router reads; older ones it finds by searching."""
OTHERS_SHOWN = 5
"""Other recent threads the orchestrator is told of, so it knows what it could read."""
READ_SHOWN = 8000
"""Characters of a thread `read_thread` shows: its end, where the answers are."""


@dataclass(frozen=True)
class Route:
    """Where an event goes."""

    thread: Thread
    closes: bool = False
    """What the owner said wraps the thread up: once answered, it's closed and summarised."""


class Threads:
    def __init__(
        self, store: ThreadStore, router: Router, summariser: Summariser, clock: Callable[[], float] = time.time
    ):
        self._store = store
        self._router = router
        self._summariser = summariser
        self._clock = clock
        self._summarising = asyncio.Lock()  # one at a time: they're background work, never urgent
        self._pending: set[asyncio.Task] = set()

    async def route(self, event: Event) -> Route:
        """The thread `event` belongs in, made or reopened as needed. Never fails: if the router does, the latest."""
        if isinstance(event, TaskUpdate) and event.task.thread and (thread := self._store.get(event.task.thread)):
            return Route(thread)  # back where it was asked for, however the conversation has moved on
        if isinstance(event, OwnerMessage):
            return await self._choose(event)
        return Route(self._store.latest() or self._store.new(_title(event)))  # a notice, or an old task

    def history(self, thread: Thread) -> list[ModelMessage]:
        return self._store.history(thread.id)

    def save(self, route: Route, messages: list[ModelMessage]) -> None:
        """The thread's history after a turn; closed and summarised (in the background) if the owner wrapped it up."""
        self._store.save(route.thread.id, messages, _size(messages))
        if route.closes:
            self._store.close(route.thread.id)
            self._in_background(self._summarise(route.thread.id))

    def describe(self, thread: Thread) -> str:
        """For the orchestrator's situation: the thread it's in, what it carries on from, and the others recent."""
        now = self._clock()
        about = [f'You\'re in thread #{thread.id}, "{thread.title}", started {ago(thread.created, now)}.']
        if thread.continues and (before := self._store.get(thread.continues)):
            about.append(f'It carries on #{before.id}, "{before.title}", which went:\n{before.summary}')
        others = [t for t in self._store.recent(OTHERS_SHOWN + 1) if t.id != thread.id][:OTHERS_SHOWN]
        if others:
            listed = "; ".join(
                f'#{t.id} "{t.title}" ({"open" if t.open else "closed"}, {ago(t.updated, now)})' for t in others
            )
            about.append(f"Other recent threads (search_threads and read_thread for what was said in any): {listed}.")
        return "\n".join(about)

    @property
    def tools(self) -> FunctionToolset:
        """`search_threads` and `read_thread`, for the orchestrator, when the owner refers to another conversation."""
        store = self._store

        async def search_threads(words: str) -> str:
            """Every conversation thread ever, by words in it ("india flights"), best match first."""
            return search(store, words, self._clock())

        async def read_thread(id: int) -> str:
            """What was said in another thread, and its summary."""
            if not (thread := store.get(id)):
                return f"There's no thread #{id}."
            said = "\n".join(lines(store.history(id)))
            if len(said) > READ_SHOWN:
                said = "…" + said[-READ_SHOWN:]
            summary = f"Summary: {thread.summary}\n" if thread.summary else ""
            return f'#{id} "{thread.title}" ({ago(thread.updated, self._clock())}).\n{summary}{said}'

        return FunctionToolset([search_threads, read_thread])

    async def watch(self) -> None:
        """Summarises finished threads as they finish. Runs until cancelled."""
        while True:
            await self.tidy()
            await asyncio.sleep(CHECK.total_seconds())

    async def tidy(self) -> None:
        """Summarises (and closes) threads left quiet, and closed ones said more in since their summary."""
        for thread in self._store.to_summarise(self._clock() - QUIET.total_seconds()):
            await self._summarise(thread.id)

    async def idle(self) -> None:
        """Waits for summaries being written."""
        while self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    async def _choose(self, event: OwnerMessage) -> Route:
        said = f"(via {event.message.address.channel}) {event.message.text}" + " [image]" * len(event.message.images)
        recent = self._store.recent(RECENT)
        with activity.call("thread router", "agent", said) as reply:
            try:
                choice = await self._router.choose(said, recent)
            except Exception:  # the model: carry on where the conversation was rather than not answer
                log.exception("Couldn't route: %s", said)
                thread = recent[0] if recent else self._store.new(_title(event))
                reply(f"#{thread.id} (the router failed)")
                return Route(thread)
            reply(f"#{choice.thread}" if choice.thread else f"new: {choice.title}")
        if choice.thread is None:
            return Route(self._store.new(choice.title.strip()), choice.closes)
        thread = self._store.get(choice.thread)
        if thread.size >= FULL:
            return Route(await self._carry_on(thread), choice.closes)
        if not thread.open:
            self._store.reopen(thread.id)
            thread = self._store.get(thread.id)
        return Route(thread, choice.closes)

    async def _carry_on(self, full: Thread) -> Thread:
        """A new thread for a full one, starting from its summary (written now if it isn't fresh)."""
        if full.stale:
            await self._summarise(full.id)
        self._store.close(full.id)
        log.info("Thread #%d is full (%d tokens): carried on in a new one", full.id, full.size)
        return self._store.new(full.title, continues=full.id)

    async def _summarise(self, id: int) -> None:
        async with self._summarising:
            thread = self._store.get(id)
            as_of = self._clock()  # anything said from now on makes this summary stale
            said = lines(self._store.history(id))
            if thread.continues and (before := self._store.get(thread.continues)):
                said.insert(0, f'(Carrying on from "{before.title}": {before.summary})')
            try:
                summary = await self._summariser.summarise(thread, said)
            except Exception:  # the model: tried again on the next tidy
                log.exception("Couldn't summarise thread #%d", id)
                return
            self._store.summarise(id, summary.title.strip() or thread.title, summary.summary.strip(), as_of)

    def _in_background(self, work) -> None:
        task = asyncio.create_task(work)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)


def _size(messages: list[ModelMessage]) -> int:
    """The tokens the last model call read: the whole context, instructions and all."""
    for message in reversed(messages):
        if isinstance(message, ModelResponse) and message.usage.input_tokens:
            return message.usage.input_tokens
    return 0


def _title(event: Event) -> str:
    """For a thread nothing chose a title for: the first words of what started it."""
    match event:
        case OwnerMessage():
            text = event.message.text
        case TaskUpdate():
            text = event.task.goal
        case Notice():
            text = event.text
        case _:
            text = ""
    return " ".join(text.split()[:6]) or "Untitled"
