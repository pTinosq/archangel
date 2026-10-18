"""Tasks running in the background, so the conversation carries on while they work: on the phone, or researching.

One phone, so one phone task drives it at a time; the rest queue. Research doesn't touch the phone, so runs beside;
the reporter writes it up as a page. Every change is saved, each step the pilot reports
is recorded for watching live, and the orchestrator hears about each task that finishes, fails or needs the owner.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pydantic_ai

from agent import activity
from agent.events import Event, TaskUpdate
from agent.messages import Address
from agent.phone.pilot import Answer, Outcome, Pilot, Record
from agent.reports import Reporter
from agent.research import Researcher
from agent.store import Kind, Status, Store, Task

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
    def __init__(self, store: Store, pilot: Pilot, researcher: Researcher, reporter: Reporter, site: str = ""):
        """`site`: where Archangel's web server (and the panel) is, for links to reports."""
        self._site = site
        self._reporter = reporter
        self._store = store
        self._pilot = pilot
        self._researcher = researcher
        self._phone = asyncio.Lock()
        self._running: dict[int, asyncio.Task] = {}  # pilots, by task id
        self._inboxes: dict[int, list[str]] = {}  # the owner's word to a running pilot, read on its next step
        self._updates: set[asyncio.Task] = set()  # updates being handled
        self.notify: Callable[[Event], Awaitable[None]] | None = None

    def start(self, goal: str, report_to: Address, kind: Kind = "phone") -> Task:
        task = self._store.add_task(goal, report_to, kind)
        log.info("Task #%d (%s): %s", task.id, kind, goal)
        self._launch(task)
        return task

    def answer(self, id: int, answer: Answer) -> str:
        """Continue a task that's waiting on the owner, or tell a running one. Returns what happened, for the model."""
        if (inbox := self._inboxes.get(id)) is not None:
            inbox.append(answer.text)
            return f"Passed on to task #{id}; it sees it on its next step."
        task = self._store.task(id)
        if task and task.kind != "phone" and task.status.open:
            return f"Task #{id} is research: it can't be told anything. Cancel it and start again to change it."
        if not task or not task.status.waiting:
            return f"Task #{id} isn't open."
        paused, task.run = task.run, None
        task.status = Status.QUEUED
        self._store.save_task(task)
        self._launch(task, paused, answer)
        return f"Task #{id} continues."

    def redirect(self, id: int, to: Address) -> str:
        """Send a task's updates somewhere else from now on."""
        task = self._store.task(id)
        if not task or not task.status.open:
            return f"Task #{id} isn't running."
        self._store.report_task_to(id, to)
        return f"Task #{id} will report via {to.channel}."

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
        activity.hop("tasks", "task", f"#{task.id} {'continue' if paused else 'start'}: {task.goal}")
        if task.kind == "phone":
            self._inboxes[task.id] = []
        self._running[task.id] = asyncio.create_task(
            self._run(task, paused, answer), context=activity.in_task(task.id, "tasks")
        )

    async def _run(self, task: Task, paused: pydantic_ai.Conversation | None, answer: Answer | None) -> None:
        try:
            if task.kind != "phone":
                outcome = await self._research(task)
            else:
                async with self._phone:
                    self._started(task)
                    with activity.call("pilot", "agent", task.goal) as reply:
                        outcome = await self._pilot.run(
                            task.goal, self._recorder(task), paused, answer, inbox=self._inboxes[task.id]
                        )
                        reply(f"{outcome.status}: {outcome.report}")
        except asyncio.CancelledError:
            log.info("Task #%d cancelled", task.id)
            self._settle(task, Status.CANCELLED, "Cancelled while running.")
            return
        finally:
            del self._running[task.id]
            self._inboxes.pop(task.id, None)
        task.run = outcome.run
        task.report_to = self._store.task(task.id).report_to  # the owner may have asked for it elsewhere meanwhile
        self._settle(task, _STATUSES[outcome.status], outcome.report)
        if self.notify:
            activity.hop("orchestrator", "task", f"#{task.id} {task.status}: {task.report}")
            # Its own asyncio task: cancelling or answering the task from the update mustn't cancel the update.
            update = asyncio.create_task(self.notify(TaskUpdate(task, outcome.screen)))
            self._updates.add(update)
            update.add_done_callback(self._updates.discard)

    async def _research(self, task: Task) -> Outcome:
        self._started(task)
        self._store.add_step(task.id, "Researching.")
        with activity.call("research", "agent", task.goal) as reply:
            try:
                found = await self._researcher.research(task.goal, deep=task.kind == "deep research")
            except Exception as error:  # the model or OpenRouter: the orchestrator decides whether to retry
                log.exception("Research failed")
                return Outcome("failed", f"Research failed: {error}")
            reply(found)
        self._store.add_step(task.id, found)
        self._store.add_step(task.id, "Writing the report.")
        try:
            with activity.call("reporter", "agent", task.goal) as reply:
                report = await self._reporter.write(task.goal, found)
                reply(report.tldr)
        except Exception:  # the research is what matters; pass it on as it is
            log.exception("Couldn't write the report")
            return Outcome("done", found)
        self._store.save_page(task.id, report.html)
        return Outcome("done", f"Report: {self._site}/reports/{task.id}\ntldr: {report.tldr}")

    def _started(self, task: Task) -> None:
        task.status = Status.RUNNING
        self._store.save_task(task)

    def _settle(self, task: Task, status: Status, report: str) -> None:
        task.status, task.report = status, report
        if not status.waiting:
            task.run = None
        self._store.save_task(task)
        self._store.add_step(task.id, f"{status}: {report}")
        log.info("Task #%d %s: %s", task.id, status, report)

    def _recorder(self, task: Task) -> Record:
        return lambda text, screen: self._store.add_step(task.id, text, screen)
