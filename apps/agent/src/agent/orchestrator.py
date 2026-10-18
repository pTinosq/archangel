"""The agent the owner talks to. It manages work rather than doing it: starts phone tasks and research, searches
the web for quick answers, relays tasks' questions, decides what to do when they fail, and keeps the owner informed on whichever channel suits.

One conversation whatever the channel. Events (owner messages, task updates) are handled one at a time, in order.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models import Model

from agent import activity, history, logs
from agent.events import Event
from agent.gateway import Gateway
from agent.messages import Message, Text
from agent.models import Instructions, describe, prompt
from agent.phone.pilot import Answer
from agent.phone.relay import PhoneError
from agent.research import Researcher
from agent.store import Kind, Status, Store
from agent.tasks import Tasks

log = logging.getLogger(__name__)

TOOL_RETRIES = 3
FINISHED_TASKS_SHOWN = 5
INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()


class Orchestrator:
    def __init__(
        self,
        model: Model,
        store: Store,
        tasks: Tasks,
        gateway: Gateway,
        researcher: Researcher,
        apps: Callable[[], Awaitable[dict[str, str]]],
        instructions: Instructions = INSTRUCTIONS,
    ):
        self._store = store
        self._tasks = tasks
        self._gateway = gateway
        self._lock = asyncio.Lock()
        self._arrived_to = ""  # the situation as the event being handled arrived
        tasks.notify = self.handle
        self._agent = Agent(
            model,
            name="orchestrator",
            deps_type=Event,
            instructions=[instructions, lambda: self._arrived_to],
            retries=TOOL_RETRIES,
            capabilities=[ProcessHistory(history.recent), ProcessHistory(history.forget_old_screenshots)],
        )

        @self._agent.tool
        async def start_task(ctx: RunContext[Event], goal: str, report_via: str | None = None) -> str:
            """Start a phone task in the background; you'll get an update when it finishes or needs the owner.

            Describe the whole goal and what to find out, e.g. "Open WhatsApp and read the latest message from Alex".
            Updates come back where you're talking now. Set `report_via` only if the owner asked for the results
            elsewhere: "voice" rings them, even if they're on a call now.
            """
            return start(ctx, goal, report_via, "phone")

        @self._agent.tool
        async def research(
            ctx: RunContext[Event], question: str, deep: bool = False, report_via: str | None = None
        ) -> str:
            """Start research on the web in the background; a report (a link and a tldr) comes back. Not on the phone,
            so it runs beside phone tasks. `report_via` as for start_task.

            Normally a minute or two and a few pence: many sources, good rather than exhaustive. `deep`: exhaustive,
            ~5 minutes and ~70p. Offer it with its cost when they want it thorough; set it only once they've agreed.
            """
            if deep and not ctx.deps.from_owner:
                return "Only the owner can agree to deep research's cost. Ask them."
            return start(ctx, question, report_via, "deep research" if deep else "research")

        def start(ctx: RunContext[Event], goal: str, report_via: str | None, kind: Kind) -> str:
            report_to = ctx.deps.reply_to
            if report_via and not (report_to := gateway.owner(report_via)):
                return f"No channel {report_via!r}. Channels: {', '.join(gateway.channels)}."
            return f"Started task #{tasks.start(goal, report_to, kind).id}."

        @self._agent.tool_plain
        async def search_web(question: str) -> str:
            """Search the web, in seconds: results with their sources, to answer from. Opening times, facts, news."""
            try:
                return await researcher.search(question)
            except Exception as error:  # the model or OpenRouter; the conversation goes on
                log.exception("Search failed")
                return f"Search failed: {error}"

        @self._agent.tool_plain
        async def phone_apps() -> str:
            """The apps installed on the phone."""
            try:
                return ", ".join(await apps())
            except PhoneError as error:
                return f"Couldn't ask the phone: {error}"

        @self._agent.tool
        async def answer_task(ctx: RunContext[Event], id: int, answer: str, approve: bool = False) -> str:
            """Pass the owner's reply to a task waiting on them, or their new word to one running.

            Set `approve` only if the owner said yes to the action.
            """
            if (task := store.task(id)) and task.status == Status.NEEDS_APPROVAL and not ctx.deps.from_owner:
                return "Only the owner can say yes or no to this. Ask them."
            return tasks.answer(id, Answer(answer, approve))

        @self._agent.tool_plain
        async def redirect_task(id: int, channel: str) -> str:
            """Send a started task's updates to another channel, e.g. "text me when it's done" (sms)."""
            if not (to := gateway.owner(channel)):
                return f"No channel {channel!r}. Channels: {', '.join(gateway.channels)}."
            return tasks.redirect(id, to)

        @self._agent.tool_plain
        async def cancel_task(id: int) -> str:
            """Stop a task."""
            return tasks.cancel(id)

        @self._agent.tool
        async def send(ctx: RunContext[Event], channel: str, text: str, screenshot: bool = False) -> str:
            """Message the owner on another channel, e.g. results they asked to get by email.

            Your reply goes where you're talking now regardless; use this only for elsewhere. `screenshot` attaches
            the screen you were just shown, e.g. texting it to the owner while on a call.
            """
            if not (to := gateway.owner(channel)):
                return f"No channel {channel!r}. Channels: {', '.join(gateway.channels)}."
            if channel == ctx.deps.reply_to.channel and not screenshot:  # or the owner gets it twice
                return f"Your reply already goes via {channel}: say it there."
            images = ctx.deps.attachments() if screenshot else ()
            if screenshot and not images:
                return "There's no screenshot to send."
            await gateway.send(Message(to, (Text(text), *images)))
            return f"Sent via {channel}."

    async def handle(self, event: Event) -> None:
        async with self._lock:
            with activity.acting("orchestrator"):
                # Taken once, not before each model call: rebuilt after start_task, the list shows the new task as if
                # it had been there all along, and the model apologises for starting a duplicate.
                self._arrived_to = await self._situation()
                try:
                    messages = self._store.history()
                    log.debug("in (%d prior messages): %s", len(messages), describe(event.prompt()))
                    result = await logs.run(self._agent, prompt(event.prompt()), deps=event, message_history=messages)
                    self._store.save_history(result.all_messages())
                    log.debug("out to %s: %s", event.reply_to, result.output)
                    parts = (Text(result.output), *event.attachments())
                except Exception as error:
                    log.exception("Failed to handle: %s", describe(event.prompt()))
                    parts = (Text(event.fallback(error)),)
                await self._gateway.send(Message(event.reply_to, parts, answering=event.from_owner))

    async def _situation(self) -> str:  # async: sync ones run on a thread, away from SQLite
        tasks = self._tasks.list()[::-1]
        shown = [t for t in tasks if t.status.open] + [t for t in tasks if not t.status.open][-FINISHED_TASKS_SHOWN:]
        lines = [
            f"#{t.id} [{t.status}] {'' if t.kind == 'phone' else t.kind + ': '}{t.goal}"
            + (f" → {t.report[:300]}" if t.report else "")
            for t in shown
        ]
        return "\n".join(
            [
                f"Channels the owner can be reached on: {self._gateway.describe()}.",
                "Tasks when this arrived (open, then recently finished):"
                if lines
                else "Tasks when this arrived: none.",
                *lines,
            ]
        )
