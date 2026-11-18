"""Tasks running in the background, so the conversation carries on while they work: on a device (the phone, the PC),
researching, in the owner's online services (connectors), or on Archangel's own code (code).

One task drives each device at a time; the rest queue for it. Devices work side by side, and research and connectors
touch none, so run beside them; the reporter writes research up as a page. Every change is saved, each step a task
reports is recorded for watching live, and the orchestrator hears about each task that finishes, fails or needs the
owner.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pydantic_ai

from agent import activity
from agent.coder import Coder
from agent.events import Event, TaskUpdate
from agent.messages import Address
from agent.helper import Helper
from agent.devices.pilot import Pilot
from agent.reports import Reporter
from agent.research import Researcher
from agent.runs import Answer, Outcome, Record
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
    def __init__(
        self,
        store: Store,
        pilots: dict[Kind, Pilot],
        researcher: Researcher,
        reporter: Reporter,
        helper: Helper,
        site: str = "",
        brief: Callable[[str], Awaitable[str]] | None = None,
        coder: Coder | None = None,
    ):
        """`pilots`: one per device, by the kind of task it does ("phone", "pc"). `site`: where Archangel's web server
        (and the panel) is, for links to reports. `brief`: what the agent doing a goal should know about the owner
        (the context twin), told it as it starts."""
        self._brief = brief
        self._site = site
        self._reporter = reporter
        self._store = store
        self._pilots = pilots
        self._researcher = researcher
        self._helper = helper
        self._coder = coder
        self._carry_on: dict[int, int] = {}  # a code task's PR to carry on, by task id
        self._devices = {kind: asyncio.Lock() for kind in pilots}  # one task on each at a time
        self._running: dict[int, asyncio.Task] = {}  # by task id
        self._inboxes: dict[int, list[str]] = {}  # the owner's word to a running pilot, read on its next step
        self._said: dict[int, list[str]] = {}  # all the owner has said to an open device task (not kept over restarts)
        self._updates: set[asyncio.Task] = set()  # updates being handled
        self.notify: Callable[[Event], Awaitable[None]] | None = None

    def start(
        self, goal: str, report_to: Address, kind: Kind = "phone", thread: int | None = None, pr: int | None = None
    ) -> Task:
        """`thread`: the conversation thread asking, where its updates go. `pr`: for code, the PR to carry on."""
        task = self._store.add_task(goal, report_to, kind, thread)
        if pr:
            self._carry_on[task.id] = pr
        log.info("Task #%d (%s): %s", task.id, kind, goal)
        self._launch(task)
        return task

    def answer(self, id: int, answer: Answer) -> str:
        """Continue a task that's waiting on the owner, or tell a running one. Returns what happened, for the model."""
        if answer.text:
            self._said.setdefault(id, []).append(answer.text)
        if (inbox := self._inboxes.get(id)) is not None:
            inbox.append(answer.text)
            return f"Passed on to task #{id}; it sees it on its next step."
        task = self._store.task(id)
        if not task or not task.status.open:
            return f"Task #{id} isn't open."
        if not task.status.waiting:  # only the pilot reads what it's told as it goes
            return f"Task #{id} ({task.kind}) can't be told anything while it runs. Cancel it and start again to change it."
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

    def pick_up(self, interrupted: list[Task]) -> list[Task]:
        """At startup, with the tasks Archangel's restart interrupted: a code task's GitHub run carries on regardless
        (a deploy, often its own merge, doesn't stop it), so it's followed again to its result. Returns the rest, for
        whoever's waiting on them to hear."""
        rest = []
        for task in interrupted:
            if task.kind == "code" and task.github_run and self._coder:
                task.status, task.report = Status.QUEUED, ""
                self._store.save_task(task)
                self._store.add_step(task.id, "Archangel restarted; following the same run again.")
                self._launch(task)
            else:
                rest.append(task)
        return rest

    def list(self) -> list[Task]:
        """Newest first."""
        return self._store.tasks()

    async def idle(self) -> None:
        """Wait for every running task, and anything they trigger, to finish."""
        while self._running or self._updates:
            await asyncio.gather(*self._running.values(), *self._updates, return_exceptions=True)

    def _launch(self, task: Task, paused: pydantic_ai.Conversation | None = None, answer: Answer | None = None):
        activity.hop("tasks", "task", f"#{task.id} {'continue' if paused else 'start'}: {task.goal}")
        if task.kind in self._pilots:
            self._inboxes[task.id] = []
        self._running[task.id] = asyncio.create_task(
            self._run(task, paused, answer), context=activity.in_task(task.id, "tasks")
        )

    async def _run(self, task: Task, paused: pydantic_ai.Conversation | None, answer: Answer | None) -> None:
        try:
            if task.kind == "connectors":
                self._started(task)
                goal = await self._briefed(task, paused)
                with activity.call("helper", "agent", task.goal) as reply:
                    outcome = await self._helper.run(goal, self._recorder(task), paused, answer)
                    reply(f"{outcome.status}: {outcome.report}")
            elif task.kind == "code":
                outcome = await self._code(task)
            elif task.kind not in self._pilots:
                outcome = await self._research(task)
            else:
                goal = await self._briefed(task, paused)  # while it waits for the device
                async with self._devices[task.kind]:
                    self._started(task)
                    with activity.call(f"{task.kind} pilot", "agent", task.goal) as reply:
                        outcome = await self._pilots[task.kind].run(
                            goal,
                            self._recorder(task),
                            paused,
                            answer,
                            inbox=self._inboxes[task.id],
                            said=self._said.setdefault(task.id, []),
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

    async def _code(self, task: Task) -> Outcome:
        """Not briefed from the twin: the request goes to a public repo, and the owner's life mustn't."""
        self._started(task)
        pr = self._carry_on.pop(task.id, None)
        with activity.call("claude code", "service", task.goal) as reply:
            try:
                # Picked up after a restart, the run is followed as it is, and the branch goes unused.
                branch = await self._coder.branch(pr) if pr else f"archangel/task-{task.id}"
                worked, report = await self._coder.change(
                    task.goal,
                    branch,
                    lambda text: self._store.add_step(task.id, text),
                    lambda run: self._store.set_github_run(task.id, run),
                    task.github_run,
                )
            except Exception as error:  # GitHub: the orchestrator decides whether to retry
                log.exception("Code task failed")
                return Outcome("failed", f"Couldn't run it on GitHub: {error}")
            reply(report)
        return Outcome("done" if worked else "failed", report)

    async def _briefed(self, task: Task, paused: pydantic_ai.Conversation | None) -> str:
        """The goal, with what the context twin says bears on it. Not for a resumed run: it was told at the start.
        Not for research either: that question goes to a web search, and the owner's life needn't."""
        if paused or not self._brief:
            return task.goal
        with activity.call("twin reader", "agent", task.goal) as reply:
            briefing = await self._brief(task.goal)
            reply(briefing or "Nothing relevant.")
        if not briefing:
            return task.goal
        self._store.add_step(task.id, f"Briefing from the context twin:\n{briefing}")
        return f"{task.goal}\n\nWhat you know about the owner that bears on it (their context twin):\n{briefing}"

    def _started(self, task: Task) -> None:
        task.status = Status.RUNNING
        self._store.save_task(task)

    def _settle(self, task: Task, status: Status, report: str) -> None:
        task.status, task.report = status, report
        if not status.waiting:
            task.run = None
            self._said.pop(task.id, None)
        self._store.save_task(task)
        self._store.add_step(task.id, f"{status}: {report}")
        log.info("Task #%d %s: %s", task.id, status, report)

    def _recorder(self, task: Task) -> Record:
        return lambda text, screen: self._store.add_step(task.id, text, screen)
