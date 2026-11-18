"""Threads (M18, `docs/threads.md`): one conversation with the owner, kept as many threads, one per topic, so the
orchestrator answers each message with its thread's history rather than the last of everything.

`Threads.route(event)` says which thread an event belongs in: the router (a quick model) chooses for what the owner
says; a task's update, or something scheduled, goes to the thread that asked for it; anything else to the latest. `history` and `save` keep each
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
from agent.events import Event, Notice, OwnerMessage, Scheduled, TaskUpdate
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
RETRY = timedelta(hours=1)
"""A summary that failed is tried again after this, not every minute: a model that refuses keeps refusing."""
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
        self._failed: dict[int, float] = {}  # threads whose summary failed, and when

    @property
    def store(self) -> ThreadStore:
        return self._store

    async def route(self, event: Event) -> Route:
        """The thread `event` belongs in, made or reopened as needed, and marked as said in now. Never fails: if
        routing does, the latest thread."""
        try:
            route = await self._route(event)
        except Exception:  # the store, or something unforeseen: an answer in the wrong thread beats none
            log.exception("Couldn't route %s", type(event).__name__)
            route = Route(self._store.latest() or self._store.new(_title(event)))
        self._store.touch(route.thread.id)
        return Route(self._store.get(route.thread.id), route.closes)

    def history(self, thread: Thread) -> list[ModelMessage]:
        return self._store.history(thread.id)

    def save(self, route: Route, messages: list[ModelMessage]) -> None:
        """The thread's history after a turn; closed and summarised (in the background) if the owner wrapped it up."""
        self._store.save(route.thread.id, messages, _size(messages))
        if route.closes:
            self._store.close(route.thread.id)
            self._in_background(self._summarise(self._store.get(route.thread.id)))

    def describe(self, thread: Thread) -> str:
        """For the orchestrator's situation: the thread it's in, what it carries on from, and the others recent."""
        now = self._clock()
        about = [f'You\'re in thread #{thread.id}, "{thread.title}", started {ago(thread.created, now)}.']
        if thread.continues and (before := self._store.get(thread.continues)):
            went = (
                f"which went:\n{before.summary}"
                if before.summary
                else f"(its summary is on its way: read_thread {before.id})"
            )
            about.append(f'It carries on #{before.id}, "{before.title}", {went}')
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
        now = self._clock()
        with activity.acting("threads"):  # in the cockpit, who asked for the summary
            for thread in self._store.to_summarise(now - QUIET.total_seconds()):
                if now - self._failed.get(thread.id, 0) >= RETRY.total_seconds():
                    await self._summarise(thread)

    async def idle(self) -> None:
        """Waits for summaries being written."""
        while self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    async def _route(self, event: Event) -> Route:
        if event.thread and (thread := self._store.get(event.thread)):
            # Back where it was asked for, however the conversation has moved on; what the owner says in a closed
            # thread reopens it.
            thread = self._current(thread)
            if event.from_owner:
                if not thread.open:
                    self._store.reopen(thread.id)
                    thread = self._store.get(thread.id)
            return Route(thread)
        if isinstance(event, OwnerMessage):
            return await self._choose(event)
        return Route(self._store.latest() or self._store.new(_title(event)))  # a notice, or a task from before threads

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
        thread = self._current(self._store.get(choice.thread))
        if not thread.open:
            self._store.reopen(thread.id)
            thread = self._store.get(thread.id)
        return Route(thread, choice.closes)

    def _current(self, thread: Thread) -> Thread:
        """Where a thread's conversation is now: its latest continuation, carried on afresh if that's full."""
        while after := self._store.successor(thread.id):
            thread = after
        return self._carry_on(thread) if thread.size >= FULL else thread

    def _carry_on(self, full: Thread) -> Thread:
        """A new thread for a full one, at once; the full one's summary, which the new one starts from, follows in
        the background (the orchestrator can read the full one meanwhile)."""
        self._store.close(full.id)
        log.info("Thread #%d is full (%d tokens): carried on in a new one", full.id, full.size)
        if full.stale:
            self._in_background(self._summarise(self._store.get(full.id)))
        return self._store.new(full.title, continues=full.id)

    async def _summarise(self, found: Thread) -> None:
        """Summarises a thread found finished (and closes it), unless it's been said in since, or summed up already
        (a wrap-up and a tidy can both ask)."""
        async with self._summarising:
            thread = self._store.get(found.id)
            if thread.updated != found.updated or not thread.stale:
                return
            said = lines(self._store.history(thread.id))
            before = self._store.get(thread.continues) if thread.continues else None
            with activity.call("thread summariser", "agent", f"#{thread.id} {thread.title}") as reply:
                try:
                    summary = await self._summariser.summarise(thread, said, before.summary if before else "")
                except Exception:  # the model: tried again later
                    log.exception("Couldn't summarise thread #%d", thread.id)
                    self._failed[thread.id] = self._clock()
                    reply("failed: tried again later")
                    return
                reply(summary.summary)
            self._failed.pop(thread.id, None)
            title = summary.title.strip() or thread.title
            self._store.summarise(thread.id, title, summary.summary.strip(), thread.updated)

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
        case Scheduled():
            text = event.job.said or event.job.what
        case _:
            text = ""
    return " ".join(text.split()[:6]) or "Untitled"
