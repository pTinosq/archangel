"""What Archangel is doing right now, for watching it live (the control panel's cockpit).

Components announce each hop (a message, a model call, a phone command) the way they log: one line, no handle on
whoever is listening. Nothing here can slow or break the real work: announcing never waits or raises, and a
listener that falls behind misses notices. Nothing is stored; only the last few are kept for new listeners.

Context variables carry the ids, so nothing passes them along: the request (one owner message, and everything it
leads to, including the tasks it starts), the task, and the actor (who is speaking).
"""

import asyncio
import contextvars
import itertools
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

RECENT = 200
QUEUED_PER_LISTENER = 1000
MAX_TEXT = 200

Kind = Literal["message", "agent", "model", "tool", "phone", "task"]
"""agent, model and phone are always calls: a tx, then an rx when it's done. The rest are one-way hops."""


@dataclass(frozen=True)
class Notice:
    id: int
    time: float
    source: str
    target: str
    dir: Literal["tx", "rx"]
    """tx: a request or message going out; rx: the reply coming back."""
    kind: Kind
    text: str
    request: int | None
    task: int | None
    ok: bool = True


_actor = contextvars.ContextVar("actor", default="archangel")
_request = contextvars.ContextVar[int | None]("request", default=None)
_task = contextvars.ContextVar[int | None]("task", default=None)
_ids = itertools.count(1)
_requests = itertools.count(1)
_recent: deque[Notice] = deque(maxlen=RECENT)
_listeners: set[asyncio.Queue[Notice]] = set()


def hop(target: str, kind: Kind, text: object, source: str | None = None, dir: Literal["tx", "rx"] = "tx") -> None:
    """Something sent one way, from the current actor unless `source` says otherwise."""
    _announce(source or _actor.get(), target, dir, kind, text)


@contextmanager
def call(target: str, kind: Kind, text: object) -> Iterator[Callable[[object], None]]:
    """A request to `target` and its reply. Inside, `target` is the actor; call the yielded function to say what
    the reply was."""
    source = _actor.get()
    _announce(source, target, "tx", kind, text)
    reply: list[object] = [""]
    ok = True
    token = _actor.set(target)
    try:
        yield lambda text: reply.__setitem__(0, text)
    except BaseException as error:
        ok, reply[0] = False, f"{type(error).__name__}: {error}"
        raise
    finally:
        _actor.reset(token)
        _announce(target, source, "rx", kind, reply[0], ok)


@contextmanager
def acting(name: str) -> Iterator[None]:
    token = _actor.set(name)
    try:
        yield
    finally:
        _actor.reset(token)


def new_request(actor: str) -> contextvars.Context:
    """A context for one owner message: `asyncio.create_task(handle(...), context=new_request("gateway"))`."""
    return _context(actor, _request, next(_requests))


def in_task(id: int, actor: str) -> contextvars.Context:
    """A context for running task `id`, still part of the request that started it."""
    return _context(actor, _task, id)


async def listen(idle: float | None = None) -> AsyncIterator[Notice | None]:
    """The recent notices, then each new one as it happens. Falling behind loses notices, never holds anyone up.

    With `idle`, yields None after that many seconds without a notice, so the listener can check on itself.
    """
    queue: asyncio.Queue[Notice] = asyncio.Queue(QUEUED_PER_LISTENER)
    for notice in _recent:
        queue.put_nowait(notice)
    _listeners.add(queue)
    try:
        while True:
            try:
                async with asyncio.timeout(idle):
                    notice = await queue.get()
            except TimeoutError:
                notice = None
            yield notice
    finally:
        _listeners.discard(queue)


def _announce(source: str, target: str, dir: Literal["tx", "rx"], kind: Kind, text: object, ok: bool = True) -> None:
    try:
        text = " ".join(str(text).split())
        notice = Notice(
            next(_ids),
            time.time(),
            source,
            target,
            dir,
            kind,
            text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…",
            _request.get(),
            _task.get(),
            ok,
        )
        _recent.append(notice)
        for queue in _listeners:
            if not queue.full():
                queue.put_nowait(notice)
    except Exception:
        log.debug("Couldn't announce %s → %s", source, target, exc_info=True)  # watching must never break the work


def _context(actor: str, var: contextvars.ContextVar, value: object) -> contextvars.Context:
    context = contextvars.copy_context()
    context.run(_actor.set, actor)
    context.run(var.set, value)
    return context
