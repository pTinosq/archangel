"""Phone tasks running in the background, so the conversation carries on while the phone works.

One phone, so one task drives it at a time; the rest queue. Every change is saved, each step the pilot reports
is recorded for watching live, and the orchestrator hears about each task that finishes, fails or needs the owner.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pydantic_ai

from agent.events import Event, TaskUpdate
from agent.messages import Address
from agent.phone.pilot import Answer, Pilot, Record
from agent.store import Status, Store, Task

log = logging.getLogger(__name__)

_STATUSES = {
    "done": Status.DONE,
    "needs_approval": Status.NEEDS_APPROVAL,
    "needs_answer": Status.NEEDS_ANSWER,
    "gave_up": Status.FAILED,
    "stuck": Status.FAILED,
    "failed": Status.FAILED,
}


class Tasks:
    def __init__(self, store: Store, pilot: Pilot):
        self._store = store
        self._pilot = pilot
        self._phone = asyncio.Lock()
        self._running: dict[int, asyncio.Task] = {}  # pilots, by task id
        self._updates: set[asyncio.Task] = set()  # updates being handled
        self.notify: Callable[[Event], Awaitable[None]] | None = None

    def start(self, goal: str, report_to: Address) -> Task:
        task = self._store.add_task(goal, report_to)
        log.info("Task #%d: %s", task.id, goal)
        self._launch(task)
        return task

    def answer(self, id: int, answer: Answer) -> str:
        """Continue a task that's waiting on the owner. Returns what happened, for the model."""
        task = self._store.task(id)
        if not task or not task.status.waiting:
            return f"Task #{id} isn't waiting for an answer."
        paused, task.run = task.run, None
        task.status = Status.QUEUED
        self._store.save_task(task)
        self._launch(task, paused, answer)
        return f"Task #{id} continues."

    def cancel(self, id: int) -> str:
        task = self._store.task(id)
        if not task or not task.status.open:
            return f"Task #{id} isn't running."
        if running := self._running.get(id):
            running.cancel()
        else:  # waiting on the owner: nothing to stop, just close it
            self._settle(task, Status.CANCELLED, "Cancelled.")
        return f"Task #{id} cancelled."

    def list(self) -> list[Task]:
        """Newest first."""
        return self._store.tasks()

    async def idle(self) -> None:
        """Wait for every running task, and anything they trigger, to finish."""
        while self._running or self._updates:
            await asyncio.gather(*self._running.values(), *self._updates, return_exceptions=True)

    def _launch(self, task: Task, paused: pydantic_ai.Conversation | None = None, answer: Answer | None = None):
        self._running[task.id] = asyncio.create_task(self._run(task, paused, answer))

    async def _run(self, task: Task, paused: pydantic_ai.Conversation | None, answer: Answer | None) -> None:
        try:
            async with self._phone:
                task.status = Status.RUNNING
                self._store.save_task(task)
                outcome = await self._pilot.run(task.goal, self._recorder(task), paused, answer)
        except asyncio.CancelledError:
            log.info("Task #%d cancelled", task.id)
            self._settle(task, Status.CANCELLED, "Cancelled while running.")
            return
        finally:
            del self._running[task.id]
        task.run = outcome.run
        self._settle(task, _STATUSES[outcome.status], outcome.report)
        if self.notify:
            # Its own asyncio task: cancelling or answering the task from the update mustn't cancel the update.
            update = asyncio.create_task(self.notify(TaskUpdate(task, outcome.screen)))
            self._updates.add(update)
            update.add_done_callback(self._updates.discard)

    def _settle(self, task: Task, status: Status, report: str) -> None:
        task.status, task.report = status, report
        if not status.waiting:
            task.run = None
        self._store.save_task(task)
        self._store.add_step(task.id, f"{status}: {report}")
        log.info("Task #%d %s: %s", task.id, status, report)

    def _recorder(self, task: Task) -> Record:
        return lambda text, screen: self._store.add_step(task.id, text, screen)
