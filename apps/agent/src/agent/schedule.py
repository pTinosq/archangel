"""Later (M13): "in 15 minutes get me an Uber home", "at 20:30 text me to call Sam", "tomorrow morning ring me with
the weather", and again and again: "every weekday at 7 ring me with the weather". The orchestrator schedules what it'll
do and when; when it's due, a `Scheduled` event wakes it with the owner's words, in the thread they were said in, and
it does it then: starts the task, texts, rings.

Times are kept absolute and shown both ways ("Wed 18 Nov 08:00, in 11 h"); the model says "at" (the owner's local
time) or "in" (minutes), and code does the arithmetic. A repeating one is a cron expression in the owner's time
(models write cron well), moved on to its next time as it fires. Kept in the store, so a restart loses nothing:
whatever came due while Archangel was down fires on start (once, if it repeats), saying how late, and the orchestrator
judges whether it still makes sense.

A repeat can't run forever or pile up cost unseen: it has a number of runs (`DEFAULT_RUNS`, at most `MAX_RUNS`), and
the run that uses the last one is said to be the last, so the orchestrator tells the owner it has stopped. The
orchestrator can stop one itself (`cancel_later`) once what it was waiting for has happened.
"""

import asyncio
import contextlib
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from cronsim import CronSim, CronSimError

from agent import activity
from agent.messages import Address

log = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("Europe/London")
"""The owner's: what "at 8" and "tomorrow morning" mean."""
DEFAULT_RUNS = 50
"""How many times a repeat runs unless the model says otherwise (every 30 min: about 25 hours)."""
MAX_RUNS = 200
"""The most a repeat may be given: a loop is a model call each time, so it has a ceiling."""
CHECK = 60
"""Seconds between looks at the clock, beside being told of a change: a sleeping host can wake past a due time."""


@dataclass(frozen=True)
class Job:
    id: int
    due: float
    what: str
    """What the orchestrator said it would do then, in its own words."""
    said: str
    """The owner's words that asked for it ("" if they weren't the owner's)."""
    report_to: Address
    thread: int | None
    created: float
    every: str = ""
    """A cron expression in the owner's time if it repeats ("0 7 * * 1-5"), else ""."""
    runs: int = 0
    """Times it has fired."""
    max_runs: int = 0
    """How many times a repeat may fire in all (0 if it doesn't repeat)."""

    @property
    def last(self) -> bool:
        """Whether the run just fired was a repeat's final one."""
        return bool(self.every) and self.runs >= self.max_runs


class Schedule:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float] = time.time):
        self._db = db
        self._clock = clock
        self._changed = asyncio.Event()
        self._firing: set[asyncio.Task] = set()
        db.execute(
            """CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY, due REAL, what TEXT, said TEXT, channel TEXT, address TEXT, thread INTEGER,
                created REAL, fired REAL
            )"""
        )
        have = {column for _, column, *_ in db.execute("PRAGMA table_info(schedule)")}
        with db:
            for column, kind, default in (
                ("every", "TEXT", "''"),
                ("runs", "INTEGER", "0"),
                ("max_runs", "INTEGER", "0"),
            ):
                if column not in have:  # added as it grew
                    db.execute(f"ALTER TABLE schedule ADD COLUMN {column} {kind} NOT NULL DEFAULT {default}")

    def add(
        self,
        due: float,
        what: str,
        said: str,
        report_to: Address,
        thread: int | None,
        every: str = "",
        max_runs: int = DEFAULT_RUNS,
    ) -> Job:
        max_runs = min(max(max_runs, 1), MAX_RUNS) if every else 0
        with self._db:
            id = self._db.execute(
                "INSERT INTO schedule (due, what, said, channel, address, thread, created, every, max_runs)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (due, what, said, report_to.channel, report_to.id, thread, self._clock(), every, max_runs),
            ).lastrowid
        self._changed.set()
        return self._job(id)

    def cancel(self, id: int) -> bool:
        with self._db:
            gone = self._db.execute("DELETE FROM schedule WHERE id = ? AND fired IS NULL", (id,)).rowcount
        self._changed.set()
        return bool(gone)

    def get(self, id: int) -> Job | None:
        """A job still to come (not one already run out or cancelled)."""
        row = self._db.execute(f"SELECT {_COLUMNS} FROM schedule WHERE id = ? AND fired IS NULL", (id,)).fetchone()
        return _job(row) if row else None

    def upcoming(self) -> list[Job]:
        rows = self._db.execute(f"SELECT {_COLUMNS} FROM schedule WHERE fired IS NULL ORDER BY due").fetchall()
        return [_job(row) for row in rows]

    def describe(self) -> str:
        """For the orchestrator's situation: what's to come."""
        now = self._clock()
        return "\n".join(
            f"#{job.id} {when(job.due, now)}{repeats(job)} "
            f"(via {job.report_to.channel}): {job.what}"
            for job in self.upcoming()
        )

    async def watch(self, fire: Callable[[Job], Awaitable[None]]) -> None:
        """Fires each job as it comes due, each on its own (a busy orchestrator delays the next, never this loop).
        Runs until cancelled."""
        while True:
            self._changed.clear()
            now = self._clock()
            for job in self.upcoming():
                if job.due > now:
                    break
                # Marked (or moved on) before it's done: done twice (two Ubers) is worse than lost to a crash mid-way.
                # A repeating one missed while down fires once, not once for each time missed. Its last run
                # (the cap) is marked fired like a one-off: the loop ends there.
                job = replace(job, runs=job.runs + 1)
                with self._db:
                    if job.every and not job.last:
                        self._db.execute(
                            "UPDATE schedule SET due = ?, runs = ? WHERE id = ?",
                            (following(job.every, now), job.runs, job.id),
                        )
                    else:
                        self._db.execute(
                            "UPDATE schedule SET fired = ?, runs = ? WHERE id = ?", (now, job.runs, job.id)
                        )
                task = asyncio.create_task(self._fire(fire, job), context=activity.new_request("schedule"))
                self._firing.add(task)
                task.add_done_callback(self._firing.discard)
            soonest = next(iter(self.upcoming()), None)
            wait = min(CHECK, soonest.due - now) if soonest else CHECK
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), max(wait, 0))

    async def _fire(self, fire: Callable[[Job], Awaitable[None]], job: Job) -> None:
        activity.hop("orchestrator", "message", f"#{job.id} due: {job.what}")
        try:
            await fire(job)
        except Exception:
            log.exception("Scheduled #%d failed", job.id)

    def _job(self, id: int) -> Job:
        return _job(self._db.execute(f"SELECT {_COLUMNS} FROM schedule WHERE id = ?", (id,)).fetchone())


_COLUMNS = "id, due, what, said, channel, address, thread, created, every, runs, max_runs"


def _job(row: tuple) -> Job:
    id, due, what, said, channel, address, thread, created, every, runs, max_runs = row
    return Job(id, due, what, said, Address(channel, address), thread, created, every, runs, max_runs)


def repeats(job: Job) -> str:
    """ ", repeats 0 7 * * 1-5 (run 3 of 50)", or "" for a one-off."""
    return f", repeats {job.every} (run {job.runs + 1} of {job.max_runs})" if job.every else ""


def following(every: str, after: float) -> float:
    """The next time cron expression `every`, in the owner's time, comes round after `after`."""
    return next(CronSim(every, datetime.fromtimestamp(after, TIMEZONE))).timestamp()


def local(t: float) -> str:
    """ "Wed 18 Nov 08:00", in the owner's time."""
    return datetime.fromtimestamp(t, TIMEZONE).strftime("%a %-d %b %H:%M")


def when(t: float, now: float) -> str:
    """ "Wed 18 Nov 08:00, in 11 h 20 min"."""
    minutes = round((t - now) / 60)
    if minutes < 1:
        return f"{local(t)}, now"
    if minutes < 60:
        return f"{local(t)}, in {minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{local(t)}, in {hours} h" + (f" {minutes} min" if minutes else "")
    return f"{local(t)}, in {round(hours / 24)} days"


def parse(at: str | None, in_minutes: float | None, every: str | None, now: float) -> float:
    """The first due time from what the model gave: `at`, the owner's local time ("2026-11-18 08:00"), or
    `in_minutes`; or, repeating `every` (cron), its next time unless `at` or `in_minutes` says when to start.
    Raises ValueError, said back to the model, if it's unreadable, past, or not exactly one time."""
    if every:
        try:
            first = following(every, now)
        except CronSimError as error:
            raise ValueError(f"Couldn't read {every!r} as cron ({error}): e.g. 0 7 * * 1-5.") from None
        if at is None and in_minutes is None:
            return first
    if (at is None) == (in_minutes is None):
        raise ValueError("Give either `at` or `in_minutes`.")
    if in_minutes is not None:
        due = now + in_minutes * 60
    else:
        try:
            moment = datetime.fromisoformat(at)
        except ValueError:
            raise ValueError(f"Couldn't read {at!r}: give it as 2026-11-18 08:00.") from None
        due = (moment if moment.tzinfo else moment.replace(tzinfo=TIMEZONE)).timestamp()
    if due < now - 60:
        raise ValueError(f"That's past: it's {local(now)} now.")
    return due
