"""The agent the owner talks to. It manages work rather than doing it: starts phone tasks, relays their
questions, decides what to do when they fail, and keeps the owner informed.

It runs on two kinds of trigger, one at a time per conversation: a message from the owner, and an update
from one of its tasks.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent, BinaryContent, RunContext, UserContent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models import Model

from agent import history, logs
from agent.gateway import Reply
from agent.messages import Conversation, Image, Message, Part, Text
from agent.phone.pilot import Answer
from agent.phone.relay import Screenshot
from agent.store import Status, Store, Task
from agent.tasks import Tasks

log = logging.getLogger(__name__)

TOOL_RETRIES = 3
FINISHED_TASKS_SHOWN = 5
INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()


@dataclass(frozen=True)
class _Turn:
    conversation: Conversation
    from_owner: bool
    """False when a task update triggered this turn: nothing in it is the owner's word."""


class Orchestrator:
    def __init__(self, model: Model, store: Store, tasks: Tasks):
        self._store = store
        self._tasks = tasks
        self._locks: defaultdict[Conversation, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._replies: dict[Conversation, Reply] = {}
        tasks.notify = self._on_task
        self._agent = Agent(
            model,
            deps_type=_Turn,
            instructions=[INSTRUCTIONS, self._task_list],
            retries=TOOL_RETRIES,
            capabilities=[ProcessHistory(history.recent), ProcessHistory(history.forget_old_screenshots)],
        )

        @self._agent.tool
        async def start_task(ctx: RunContext[_Turn], goal: str) -> str:
            """Start a phone task in the background; you'll get an update when it finishes or needs the owner.

            Describe the whole goal and what to find out, e.g. "Open WhatsApp and read the latest message from Alex".
            """
            return f"Started task #{tasks.start(ctx.deps.conversation, goal).id}."

        @self._agent.tool
        async def answer_task(ctx: RunContext[_Turn], id: int, answer: str, approve: bool = False) -> str:
            """Pass the owner's reply to a task waiting on them.

            Set `approve` only if the owner said yes to the action.
            """
            if approve and not ctx.deps.from_owner:
                return "Only the owner can approve. Ask them."
            return tasks.answer(id, Answer(answer, approve))

        @self._agent.tool_plain
        async def cancel_task(id: int) -> str:
            """Stop a task."""
            return tasks.cancel(id)

    async def handle(self, message: Message, reply: Reply) -> None:
        self._replies[message.conversation] = reply
        await self._turn(_Turn(message.conversation, from_owner=True), _prompt(message.parts))

    async def _on_task(self, task: Task, screen: Screenshot | None) -> None:
        update = f"[Update on task #{task.id}, not from the owner] {task.status}: {task.report}"
        # The owner should see what they're asked to approve or answer, not just read about it.
        shown = (Image(screen.jpeg, "image/jpeg"),) if screen and task.status == Status.WAITING else ()
        try:
            await self._turn(_Turn(task.conversation, from_owner=False), _prompt((Text(update), *shown)), shown)
        except Exception as error:
            log.exception("Failed to handle task #%d's update", task.id)
            await self._reply(task.conversation, (Text(f"Task #{task.id}: {task.report} ({type(error).__name__})"),))

    async def _turn(self, turn: _Turn, prompt: list[UserContent], attachments: tuple[Part, ...] = ()) -> None:
        async with self._locks[turn.conversation]:
            messages = self._store.history(turn.conversation)
            log.debug("in %s (%d prior messages): %s", turn.conversation, len(messages), _describe(prompt))
            result = await logs.run(self._agent, prompt, deps=turn, message_history=messages)
            self._store.save_history(turn.conversation, result.all_messages())
            log.debug("out %s: %s", turn.conversation, result.output)
            await self._reply(turn.conversation, (Text(result.output), *attachments))

    async def _reply(self, conversation: Conversation, parts: tuple[Part, ...]) -> None:
        if reply := self._replies.get(conversation):
            await reply(Message(conversation, parts))
        else:
            log.warning("No way to reach %s yet; dropped: %s", conversation, parts)

    async def _task_list(self, ctx: RunContext[_Turn]) -> str:  # async: sync ones run on a thread, away from SQLite
        tasks = self._tasks.list(ctx.deps.conversation)
        shown = [t for t in tasks if t.status.open] + [t for t in tasks if not t.status.open][-FINISHED_TASKS_SHOWN:]
        if not shown:
            return "Tasks: none yet."
        lines = [f"#{t.id} [{t.status}] {t.goal}" + (f" → {t.report[:300]}" if t.report else "") for t in shown]
        return "Tasks (open, then recently finished):\n" + "\n".join(lines)


def _prompt(parts: tuple[Part, ...]) -> list[UserContent]:
    return [
        part.text if isinstance(part, Text) else BinaryContent(data=part.data, media_type=part.media_type)
        for part in parts
    ]


def _describe(prompt: list[UserContent]) -> str:
    return " ".join(item if isinstance(item, str) else "[image]" for item in prompt)
