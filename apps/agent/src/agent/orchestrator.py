"""The agent the owner talks to. It manages work rather than doing it: starts phone tasks, research and tasks in the
owner's connected services, searches the web for quick answers, relays tasks' questions, decides what to do when they
fail, and keeps the owner informed on whichever channel suits.

One conversation whatever the channel. Events (owner messages, task updates, notices) are handled one at a time, in
order.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models import Model

from agent import activity, history, logs
from agent.connectors import Connectors
from agent.events import Event
from agent.gateway import Gateway
from agent.location import Whereabouts
from agent.messages import Message, Text
from agent.models import Instructions, describe, prompt
from agent.phone.relay import PhoneError
from agent.research import Researcher
from agent.runs import Answer
from agent.store import Kind, Status, Store
from agent.tasks import Tasks
from agent.twin import Twin

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
        connectors: Connectors,
        apps: Callable[[], Awaitable[dict[str, str]]],
        whereabouts: Whereabouts,
        instructions: Instructions = INSTRUCTIONS,
        twin: Twin | None = None,
    ):
        self._store = store
        self._tasks = tasks
        self._gateway = gateway
        self._connectors = connectors
        self._lock = asyncio.Lock()
        self._arrived_to = ""  # the situation as the event being handled arrived
        self._ends = False  # the reply being written is the last word on a call
        tasks.notify = whereabouts.notify = self.handle
        self._agent = Agent(
            model,
            name="orchestrator",
            deps_type=Event,
            instructions=[instructions, lambda: self._arrived_to],
            retries=TOOL_RETRIES,
            capabilities=[ProcessHistory(history.recent), ProcessHistory(history.forget_old_screenshots)],
            toolsets=[twin.tools] if twin else [],
        )
        self._twin = twin

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

        @self._agent.tool
        async def use_connectors(ctx: RunContext[Event], goal: str, report_via: str | None = None) -> str:
            """Start a task in the owner's connected services (listed above) in the background, e.g. "Summarise
            today's unread emails". Quicker and surer than the phone, and runs beside it. `report_via` as for
            start_task."""
            return start(ctx, goal, report_via, "connectors")

        def start(ctx: RunContext[Event], goal: str, report_via: str | None, kind: Kind) -> str:
            report_to = ctx.deps.reply_to
            if report_via and not (report_to := gateway.owner(report_via)):
                return no_channel(report_via)
            return f"Started task #{tasks.start(goal, report_to, kind).id}."

        def no_channel(name: str) -> str:
            return f"No channel {name!r}. Channels: {', '.join(gateway.channels)}."

        @self._agent.tool_plain
        async def search_web(question: str) -> str:
            """Search the web, in seconds: results with their sources, to answer from. Opening times, facts, news."""
            try:
                return await researcher.search(question)
            except Exception as error:  # the model or OpenRouter; the conversation goes on
                log.exception("Search failed")
                return f"Search failed: {error}"

        @self._agent.tool_plain
        async def read_page(url: str) -> str:
            """Read a web page in full, in seconds: e.g. the list or page a search result only points to."""
            try:
                return await researcher.read(url)
            except Exception as error:  # Tavily; the conversation goes on
                log.exception("Reading a page failed")
                return f"Couldn't read it: {error}"

        @self._agent.tool_plain
        async def phone_apps() -> str:
            """The apps installed on the phone."""
            try:
                return ", ".join(await apps())
            except PhoneError as error:
                return f"Couldn't ask the phone: {error}"

        @self._agent.tool
        async def owner_location(ctx: RunContext[Event]) -> str:
            """Where the owner is, for "near me" or a pickup (the phone is at home): where they last shared, how long
            ago, and a link that shares it now in one tap. Text them the link when you need it fresh; it comes back
            as a notice."""
            last = whereabouts.latest()
            known = f"Last shared {_ago(last.time)}: {last}." if last else "Never shared."
            link = whereabouts.link(ctx.deps.reply_to)
            return f"{known} Link: {link}" if link else f"{known} No link here (no public address): ask them."

        @self._agent.tool
        async def answer_task(ctx: RunContext[Event], id: int, answer: str, approve: bool = False) -> str:
            """Pass the owner's reply to a task waiting on them, or their new word to one running.

            Set `approve` only if the owner said yes to the action.
            """
            if (task := store.task(id)) and task.status == Status.NEEDS_APPROVAL and not ctx.deps.from_owner:
                return "Only the owner can say yes or no to this. Ask them."
            return tasks.answer(id, Answer(answer, approve))

        @self._agent.tool
        async def report_visibility(ctx: RunContext[Event], id: int, public: bool | None = None) -> str:
            """Whether a report's link opens for anyone (public), or only for the owner, signed in (private, the
            default). Set `public` to change it: public to share it with someone."""
            if not store.has_page(id):
                return f"Task #{id} has no report."
            if public and not ctx.deps.from_owner:
                return "Only the owner can make a report public. Ask them."
            if public is not None:
                store.make_public(id, public)
            if store.task(id).public:
                return f"Report #{id} is public: anyone with its link can read it."
            return f"Report #{id} is private: only the owner can open it, signed in."

        @self._agent.tool_plain
        async def redirect_task(id: int, channel: str) -> str:
            """Send a started task's updates to another channel, e.g. "text me when it's done" (sms)."""
            if not (to := gateway.owner(channel)):
                return no_channel(channel)
            return tasks.redirect(id, to)

        @self._agent.tool_plain
        async def cancel_task(id: int) -> str:
            """Stop a task."""
            return tasks.cancel(id)

        @self._agent.tool_plain
        async def hang_up() -> str:
            """On a call, hang up once your reply is said: they've said bye, or asked you to."""
            self._ends = True
            return "It hangs up after your reply."

        @self._agent.tool
        async def send(ctx: RunContext[Event], channel: str, text: str, screenshot: bool = False) -> str:
            """Message the owner on another channel, e.g. results they asked to get by email.

            Your reply goes where you're talking now regardless; use this only for elsewhere. `screenshot` attaches
            the screen you were just shown, e.g. texting it to the owner while on a call.
            """
            if not (to := gateway.owner(channel)):
                return no_channel(channel)
            if channel == ctx.deps.reply_to.channel and not screenshot:  # or the owner gets it twice
                return f"Your reply already goes via {channel}: say it there."
            images = ctx.deps.attachments() if screenshot else ()
            if screenshot and not images:
                return "There's no screenshot to send."
            await gateway.send(Message(to, (Text(text), *images)))
            return f"Sent via {channel}."

        if twin:

            @self._agent.tool_plain
            async def remember(what: str) -> str:
                """The owner asked you to remember something lasting about them ("remember I hate morning flights"):
                their words. Kept from now on, everywhere."""
                twin.remember(f"remember {what}")
                return "Noting it."

            @self._agent.tool_plain
            async def forget(what: str) -> str:
                """The owner asked you to forget something you'd kept about them: their words."""
                twin.remember(f"forget {what}")
                return "Forgetting it."

    async def handle(self, event: Event) -> None:
        async with self._lock:
            with activity.acting("orchestrator"):
                # Taken once, not before each model call: rebuilt after start_task, the list shows the new task as if
                # it had been there all along, and the model apologises for starting a duplicate.
                self._arrived_to = self._situation()
                self._ends = False
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
                await self._gateway.send(Message(event.reply_to, parts, answering=event.from_owner, ends=self._ends))

    def _situation(self) -> str:
        tasks = self._tasks.list()[::-1]
        shown = [t for t in tasks if t.status.open] + [t for t in tasks if not t.status.open][-FINISHED_TASKS_SHOWN:]
        lines = [
            f"#{t.id} [{t.status}] {'' if t.kind == 'phone' else t.kind + ': '}{t.goal}"
            + (f" → {t.report[:300]}" if t.report else "")
            + (" (report public)" if t.public else "")
            for t in shown
        ]
        about = [f"The owner, from their context twin (ask_twin for more):\n{self._twin.root()}"] if self._twin else []
        return "\n".join(
            [
                *about,
                f"Channels the owner can be reached on: {self._gateway.describe()}.",
                "Connected services, by profile: "
                + (self._connectors.describe() or "none (added on the panel, under Connectors)")
                + ".",
                "Tasks when this arrived (open, then recently finished):"
                if lines
                else "Tasks when this arrived: none.",
                *lines,
            ]
        )


def _ago(then: float) -> str:
    minutes = round((time.time() - then) / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60} h {minutes % 60} min ago" if minutes < 24 * 60 else f"{minutes // (24 * 60)} days ago"
