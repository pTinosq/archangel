"""The context twin: the owner's second brain, a graph that grows from what they say. See docs/twin.md.

The reader briefs each task before it starts and answers agents' questions mid-task (`ask_twin`). The writer reads each conversation once it has gone quiet (the owner's answers to tasks are said in it too), and
what the owner asks to be remembered or forgotten at once, one episode at a time.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic_ai import FunctionToolset

from agent import activity
from agent.events import Event, Notice
from agent.messages import Address
from agent.store import Line, Store
from agent.twin.graph import ROOT, Graph
from agent.twin.reader import Reader
from agent.twin.writer import Episode, Writer

log = logging.getLogger(__name__)

QUIET = 10 * 60
"""Seconds without a message before a conversation is read: what was said settles, and it's read once, whole."""
CHECK = 60


class Twin:
    def __init__(
        self,
        store: Store,
        graph: Graph,
        reader: Reader,
        writer: Writer,
        owner: Callable[[str], Address | None],
    ):
        """`owner`: the owner's address on a channel, for telling them what was noted."""
        self.graph = graph
        self._store, self._reader, self._writer, self._owner = store, reader, writer, owner
        self._writing = asyncio.Lock()  # one episode at a time: two mustn't edit the same node at once
        self._pending: set[asyncio.Task] = set()
        self.notify: Callable[[Event], Awaitable[None]] | None = None
        store.db.execute("CREATE TABLE IF NOT EXISTS twin_read (id INTEGER PRIMARY KEY CHECK (id = 1), line INTEGER)")
        if self._read_up_to() is None:  # from now: the transcript before the twin isn't read back
            lines = store.lines()
            self._mark(lines[-1].id if lines else 0)

    def root(self) -> str:
        """The root page and where it links: what the orchestrator always has in mind."""
        return self.graph.open(ROOT)

    async def brief(self, goal: str) -> str:
        """What an agent about to do `goal` should know about the owner; "" if nothing, or if reading failed (the
        task goes ahead without)."""
        try:
            return await self._reader.brief(goal)
        except Exception:
            log.exception("Couldn't brief: %s", goal)
            return ""

    async def hear(self, said: str) -> str:
        """What bears on what the owner just said, for the orchestrator before it answers; "" if nothing, or if
        reading failed."""
        with activity.call("twin reader", "agent", said) as reply:
            try:
                found = await self._reader.hear(said)
            except Exception:
                log.exception("Couldn't read the twin for: %s", said)
                found = ""
            reply(found or "Nothing relevant.")
            return found

    @property
    def tools(self) -> FunctionToolset:
        """`ask_twin`, for any agent that may need to know something about the owner mid-task."""

        async def ask_twin(question: str) -> str:
            """Ask what the owner would want, or a fact about them you don't have in front of you: "a sign-in
            page: which account?", "Mum's name?". Not for what only they can decide now: ask them."""
            with activity.call("twin reader", "agent", question) as reply:
                try:
                    answer = await self._reader.ask(question)
                except Exception as error:  # the model: the agent carries on without
                    log.exception("Couldn't answer: %s", question)
                    answer = f"Couldn't read the twin ({error})."
                reply(answer)
                return answer

        return FunctionToolset([ask_twin])

    def remember(self, what: str) -> None:
        """The owner asked to remember or forget something: written now, in the background."""
        self._write(Episode(f"The owner asked: {what}", "chat"))

    async def watch(self) -> None:
        """Reads each conversation once it has been quiet for `QUIET`. Runs until cancelled."""
        while True:
            await self.read_quiet()
            await asyncio.sleep(CHECK)

    async def read_quiet(self, now: float | None = None) -> None:
        lines = self._store.lines(self._read_up_to() or 0)
        if not lines or (now or time.time()) - lines[-1].time < QUIET:
            return
        self._mark(lines[-1].id)
        said = [line for line in lines if line.sender == "owner"]
        if said:
            with activity.acting("transcript"):  # in the cockpit, what the writer was handed
                done = self._write(Episode(_conversation(lines), "chat"), self._tell(said[-1].channel))
            await done.wait()

    async def idle(self) -> None:
        while self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    def _write(self, episode: Episode, tell: Address | None = None) -> asyncio.Event:
        done = asyncio.Event()

        async def write() -> None:
            async with self._writing:
                with activity.call("twin writer", "agent", episode.text) as reply:
                    try:
                        note = await self._writer.notice(episode)
                    except Exception:  # the model: what was said is still in the transcript and the history
                        log.exception("The twin couldn't read %s", episode.origin)
                        note = ""
                    reply(note or "Nothing.")
            done.set()
            if note and tell and self.notify:
                await self.notify(Notice(f"You noted, from what the owner said: {note} Tell them, briefly.", tell))

        task = asyncio.create_task(write())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return done

    def _tell(self, channel: str) -> Address | None:
        # Not a call: noting something isn't worth ringing them for.
        return self._owner("sms" if channel == "voice" else channel) or self._owner("panel")

    def _read_up_to(self) -> int | None:
        row = self._store.db.execute("SELECT line FROM twin_read").fetchone()
        return row and row[0]

    def _mark(self, line: int) -> None:
        with self._store.db:
            self._store.db.execute("INSERT OR REPLACE INTO twin_read VALUES (1, ?)", (line,))


def _conversation(lines: list[Line]) -> str:
    said = [
        f"[{datetime.fromtimestamp(line.time):%a %d %b %Y %H:%M}] "
        + (f"Owner via {line.channel}" if line.sender == "owner" else "Archangel")
        + f": {line.text}"
        for line in lines
    ]
    return "A conversation with the owner:\n" + "\n".join(said)
