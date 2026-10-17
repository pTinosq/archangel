"""Phone tasks running in the background, so the conversation carries on while the phone works.

One phone, so one task drives it at a time; the rest queue. Every change is saved, and the orchestrator
hears about each task that finishes, fails or needs the owner.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import pydantic_ai

from agent.messages import Conversation
from agent.phone.pilot import Answer, Outcome
from agent.phone.relay import Screenshot
from agent.store import Status, Store, Task

log = logging.getLogger(__name__)

Notify = Callable[[Task, Screenshot | None], Awaitable[None]]

_STATUSES = {
    "done": Status.DONE,
    "waiting": Status.WAITING,
    "gave_up": Status.FAILED,
    "stuck": Status.FAILED,
    "failed": Status.FAILED,
}


class Pilot(Protocol):
    async def run(
        self, goal: str, paused: pydantic_ai.Conversation | None = None, answer: Answer | None = None
    ) -> Outcome: ...


class Tasks:
    def __init__(self, store: Store, pilot: Pilot):
        self._store = store
        self._pilot = pilot
        self._phone = asyncio.Lock()
        self._running: dict[int, asyncio.Task] = {}  # pilots, by task id
        self._updates: set[asyncio.Task] = set()  # notifications being handled
        self.notify: Notify | None = None

    def start(self, conversation: Conversation, goal: str) -> Task:
        task = self._store.add_task(conversation, goal)
        log.info("Task #%d: %s", task.id, goal)
        self._launch(task)
        return task

    def answer(self, id: int, answer: Answer) -> str:
        """Continue a task that's waiting on the owner. Returns what happened, for the model."""
        task = self._store.task(id)
        if not task or task.status != Status.WAITING:
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
            task.status, task.report, task.run = Status.CANCELLED, "Cancelled.", None
            self._store.save_task(task)
        return f"Task #{id} cancelled."

    def list(self, conversation: Conversation) -> list[Task]:
        return self._store.tasks(conversation)

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
                outcome = await self._pilot.run(task.goal, paused, answer)
        except asyncio.CancelledError:
            log.info("Task #%d cancelled", task.id)
            task.status, task.report = Status.CANCELLED, "Cancelled while running."
            self._store.save_task(task)
            return
        finally:
            del self._running[task.id]
        task.status, task.report, task.run = _STATUSES[outcome.status], outcome.report, outcome.run
        self._store.save_task(task)
        log.info("Task #%d %s: %s", task.id, task.status, task.report)
        if self.notify:
            # Separate from the pilot's run, so cancelling or answering the task from the update can't cancel the update.
            update = asyncio.create_task(self.notify(task, outcome.screen))
            self._updates.add(update)
            update.add_done_callback(self._updates.discard)
