"""The context twin: the owner's second brain, a graph that grows from what they say. See docs/twin.md.

The writer reads each conversation once it has gone quiet (the owner's answers to tasks are said in it too), and
what the owner asks to be remembered or forgotten at once, one episode at a time.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from agent.events import Event, Notice
from agent.messages import Address
from agent.store import Line, Store
from agent.twin.graph import Graph
from agent.twin.writer import Episode, Writer

log = logging.getLogger(__name__)

QUIET = 10 * 60
"""Seconds without a message before a conversation is read: what was said settles, and it's read once, whole."""
CHECK = 60


class Twin:
    def __init__(self, store: Store, graph: Graph, writer: Writer, owner: Callable[[str], Address | None]):
        """`owner`: the owner's address on a channel, for telling them what was noted."""
        self.graph = graph
        self._store, self._writer, self._owner = store, writer, owner
        self._writing = asyncio.Lock()  # one episode at a time: two mustn't edit the same node at once
        self._pending: set[asyncio.Task] = set()
        self.notify: Callable[[Event], Awaitable[None]] | None = None
        store.db.execute("CREATE TABLE IF NOT EXISTS twin_read (id INTEGER PRIMARY KEY CHECK (id = 1), line INTEGER)")
        if self._read_up_to() is None:  # from now: the transcript before the twin isn't read back
            lines = store.lines()
            self._mark(lines[-1].id if lines else 0)

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
            await self._write(Episode(_conversation(lines), "chat"), self._tell(said[-1].channel)).wait()

    async def idle(self) -> None:
        while self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    def _write(self, episode: Episode, tell: Address | None = None) -> asyncio.Event:
        done = asyncio.Event()

        async def write() -> None:
            async with self._writing:
                try:
                    note = await self._writer.notice(episode)
                except Exception:  # the model: what was said is still in the transcript and the history
                    log.exception("The twin couldn't read %s", episode.origin)
                    note = ""
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
