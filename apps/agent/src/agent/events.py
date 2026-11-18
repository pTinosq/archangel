"""Everything that wakes the orchestrator. Each kind says what the model sees, where the answer goes by default,
and whether it carries the owner's authority. New triggers (an incoming call) are new kinds here.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from agent.messages import Address, Image, Message, Part
from agent.schedule import Job, following, local
from agent.store import Task


class Event(ABC):
    reply_to: Address
    """Where the orchestrator's answer goes unless it decides otherwise."""
    from_owner: bool
    """Only events from the owner may approve anything."""

    @property
    def owner_asked(self) -> bool:
        """The owner asked for this in their own words: enough for what only they may start (`improve`, `merge`, deep
        research, a public report), never for an approval, which needs them now."""
        return self.from_owner

    @abstractmethod
    def prompt(self) -> list[Part | str]:
        """What the orchestrator model is shown."""

    @property
    def thread(self) -> int | None:
        """The conversation thread it belongs in, if that's known already: where its task or schedule was asked for."""
        return None

    def attachments(self) -> tuple[Part, ...]:
        """Sent to the owner along with the answer."""
        return ()

    @abstractmethod
    def fallback(self, error: Exception) -> str:
        """What the owner hears if the orchestrator itself fails."""


@dataclass(frozen=True)
class OwnerMessage(Event):
    message: Message
    line: int | None = None
    """Its line in the transcript, filed under its thread once that's known."""
    from_owner = True

    @property
    def reply_to(self) -> Address:
        return self.message.address

    @property
    def thread(self) -> int | None:
        """Set when the owner chose the thread (said it with that thread chosen): then nothing routes it."""
        return self.message.thread

    def prompt(self) -> list[Part | str]:
        return [f"(via {self.message.address.channel})", *self.message.parts]

    def fallback(self, error: Exception) -> str:
        return "Sorry, something went wrong on my side. Could you say that again?"  # the error is in the log


@dataclass(frozen=True)
class TaskUpdate(Event):
    task: Task
    screen: Image | None = None
    from_owner = False

    @property
    def reply_to(self) -> Address:
        return self.task.report_to

    @property
    def thread(self) -> int | None:
        return self.task.thread

    def prompt(self) -> list[Part | str]:
        task = self.task
        update = f"[Update on task #{task.id}, not from the owner; your reply goes via {task.report_to.channel}]"
        update += f" {task.status}: {task.report}"
        return [update, *self.attachments()]

    def attachments(self) -> tuple[Part, ...]:
        # The owner should see what they're asked to approve or answer, not just read about it.
        return (self.screen,) if self.screen and self.task.status.waiting else ()

    def fallback(self, error: Exception) -> str:
        return f'An update on "{self.task.goal}": {self.task.report}'


@dataclass(frozen=True)
class Notice(Event):
    """Something a channel saw happen, e.g. that a call Archangel made went to voicemail. Not from the owner: what,
    if anything, to tell them is the orchestrator's call."""

    text: str
    reply_to: Address
    from_owner = False

    def prompt(self) -> list[Part | str]:
        return [f"[Notice, not from the owner; your reply, if any, goes via {self.reply_to.channel}] {self.text}"]

    def fallback(self, error: Exception) -> str:
        return ""  # nothing the owner needs from a notice the orchestrator couldn't think about


LATE = 120
"""Seconds past due after which it's said to be late (Archangel was down): it may no longer make sense."""
LAST_REPORT = 1500
"""Characters of the last run's task report a repeat's wake-up shows: enough for the answer and the way to it."""


@dataclass(frozen=True)
class Scheduled(Event):
    """Something the orchestrator scheduled, now due. Not the owner speaking now, though they asked for it."""

    job: Job
    last_task: Task | None = None
    """The task the repeat's previous run started (`Job.last_task`), as it stands now."""
    from_owner = False

    @property
    def owner_asked(self) -> bool:
        return bool(self.job.said)  # set only from the owner's own message

    @property
    def reply_to(self) -> Address:
        return self.job.report_to

    @property
    def thread(self) -> int | None:
        return self.job.thread

    def prompt(self) -> list[Part | str]:
        job, now = self.job, time.time()
        asked = f'they said "{job.said}"' if job.said else "you scheduled it"
        due = f"It's due now ({local(job.due)})"
        if now - job.due > LATE:
            due = (
                f"It was due at {local(job.due)}, but Archangel was down: it's {local(now)}. Do it only if it still "
                "makes sense; else tell them"
            )
        if job.every and job.last:  # the store has already ended it
            due += (
                f"; it repeats ({job.every}) but this was its last run, {job.runs} of {job.max_runs}: "
                "it has stopped, so tell the owner, whatever the outcome"
            )
        elif job.every:  # the store has already moved it on
            due += (
                f"; it repeats ({job.every}), run {job.runs} of {job.max_runs}, "
                f"next {local(following(job.every, now))}. Nothing you say reaches the owner from this run unless it "
                f"ends the repeat: once what it waits for has happened (or it's no longer wanted), stop it with "
                f"cancel_later({job.id}) and say so, once; until then, show progress with note_progress"
            )
        said = f"[Scheduled #{job.id}, not from the owner now: at {local(job.created)} {asked}. {due}."
        if job.every:  # without it, each run works the same route out again and can't tell nothing has changed
            if task := self.last_task:
                report = task.report if len(task.report) <= LAST_REPORT else task.report[:LAST_REPORT] + "…"
                said += (
                    f" Last run's task #{task.id} [{task.status}] {task.goal} → {report or '(no report yet)'}. "
                    "A new task should be told the way that one got there."
                )
            if job.progress:
                said += f' Last noted: "{job.progress}".'
        return [f"{said} Your reply goes via {job.report_to.channel}.] Do it now: {job.what}"]

    def fallback(self, error: Exception) -> str:
        job = self.job
        return (
            f'Something went wrong on my side with what you asked for at {local(job.created)}: "{job.said or job.what}"'
        )
