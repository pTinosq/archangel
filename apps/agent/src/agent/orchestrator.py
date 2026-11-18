"""The agent the owner talks to. It manages work rather than doing it: starts tasks on the phone or PC, research and
tasks in the owner's connected services, answers quick questions from live data (`agent.tools`) or the web, relays
tasks' questions, decides what to
do when they fail, and keeps the owner informed on whichever channel suits.

One conversation whatever the channel, kept as threads, one per topic: each event is answered in its thread, seeing
that thread's history rather than the last of everything (`agent.threads`). Events (owner messages, task updates,
notices) are handled one at a time, in order.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models import Model

from agent import activity, history, logs
from agent.coder import Coder
from agent.connectors import Connectors
from agent.devices.relay import Device, DeviceError
from agent.events import Event, OwnerMessage, Scheduled
from agent.gateway import Gateway
from agent.location import Whereabouts
from agent.messages import Message, Text
from agent.models import Instructions, describe, prompt
from agent.research import Researcher
from agent.runs import Answer
from agent.schedule import DEFAULT_RUNS, TIMEZONE, Job, Schedule, parse, when
from agent.store import Kind, Status, Store
from agent.styles import Audience, Styles
from agent.tasks import Tasks
from agent.threads import Threads
from agent.threads.store import Thread
from agent.threads.text import ago
from agent.tools import Tools
from agent.twin import Twin

log = logging.getLogger(__name__)

TOOL_RETRIES = 3
FINISHED_TASKS_SHOWN = 5
INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()


def _own_code(goal: str) -> bool:
    """A device task about Archangel's own repo: a model that once sent one to the PC, signed in to GitHub, kept doing
    so in that thread whatever its prompt said."""
    goal = goal.lower()
    return "archangel" in goal and any(word in goal for word in ("repo", "readme", "github", "pull request"))


def _same(a: str, b: str) -> bool:
    """The same thing to do, said the same way but for case and spacing."""
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def stay_quiet(ctx: RunContext[Event]) -> str:
    """Tell the owner nothing this time, instead of replying: nothing they'd want to hear yet, like a repeat's check
    that found no change, or a step on the way to what they asked for."""
    if ctx.deps.from_owner:
        raise ModelRetry("The owner spoke to you: answer them.")
    return ""  # the gateway sends nothing for nothing


class Orchestrator:
    def __init__(
        self,
        model: Model,
        store: Store,
        tasks: Tasks,
        gateway: Gateway,
        researcher: Researcher,
        connectors: Connectors,
        devices: Mapping[str, Device],
        whereabouts: Whereabouts,
        threads: Threads,
        instructions: Instructions = INSTRUCTIONS,
        twin: Twin | None = None,
        schedule: Schedule | None = None,
        coder: Coder | None = None,
        tools: Tools | None = None,
        styles: Styles | None = None,
    ):
        self._store = store
        self._tasks = tasks
        self._threads = threads
        self._gateway = gateway
        self._connectors = connectors
        self._devices = devices  # by the kind of task done on each: "phone", "pc"
        self._lock = asyncio.Lock()
        self._arrived_to = ""  # the situation as the event being handled arrived
        self._ends = False  # the reply being written is the last word on a call
        self._stopped = False  # a scheduled repeat has ended itself in this run
        self._thread: Thread | None = None  # the thread of the event being handled
        tasks.notify = whereabouts.notify = self.handle
        self._agent = Agent(
            model,
            name="orchestrator",
            deps_type=Event,
            instructions=[instructions, lambda: self._arrived_to],
            retries=TOOL_RETRIES,
            output_type=[str, ToolOutput(stay_quiet, name="stay_quiet", max_retries=TOOL_RETRIES)],
            capabilities=[ProcessHistory(history.recent), ProcessHistory(history.forget_old_screenshots)],
            toolsets=[
                threads.tools,
                *([twin.tools] if twin else []),
                *([tools.toolset] if tools else []),
                *([styles.tools] if styles else []),
            ],
        )
        self._twin = twin
        self._styles = styles
        self._quick = tools
        self._schedule = schedule

        @self._agent.tool
        async def start_task(
            ctx: RunContext[Event], goal: str, report_via: str | None = None, on: Literal["phone", "pc"] = "phone"
        ) -> str:
            """Start a task on a device in the background, `on` the phone or the PC; you'll get an update when it
            finishes or needs the owner.

            Describe the whole goal and what to find out, e.g. "Open WhatsApp and read the latest message from Alex".
            Never for Archangel's own code (its GitHub repo, README, panel, abilities): that's improve.
            Updates come back where you're talking now. Set `report_via` only if the owner asked for the results
            elsewhere: "voice" rings them, even if they're on a call now.
            """
            if await devices[on].state() is None:
                return f"There's no {devices[on].label}: none has ever connected."
            if coder and _own_code(goal):
                return "That's Archangel's own code: use improve, not a device."
            return start(ctx, goal, report_via, on)

        @self._agent.tool
        async def research(
            ctx: RunContext[Event], question: str, deep: bool = False, report_via: str | None = None
        ) -> str:
            """Start research on the web in the background; a report (a link and a tldr) comes back. Not on a device,
            so it runs beside device tasks. `report_via` as for start_task.

            Normally a minute or two and a few pence: many sources, good rather than exhaustive. `deep`: exhaustive,
            ~5 minutes and ~70p. Offer it with its cost when they want it thorough; set it only once they've agreed.
            """
            if deep and not ctx.deps.owner_asked:
                return "Only the owner can agree to deep research's cost. Ask them."
            return start(ctx, question, report_via, "deep research" if deep else "research")

        @self._agent.tool
        async def use_connectors(ctx: RunContext[Event], goal: str, report_via: str | None = None) -> str:
            """Start a task in the owner's connected services (listed above) in the background, e.g. "Summarise
            today's unread emails". Quicker and surer than a device, and runs beside them. `report_via` as for
            start_task."""
            return start(ctx, goal, report_via, "connectors")

        def start(ctx: RunContext[Event], goal: str, report_via: str | None, kind: Kind, pr: int | None = None) -> str:
            report_to = ctx.deps.reply_to
            if report_via and not (report_to := gateway.owner(report_via)):
                return no_channel(report_via)
            task = tasks.start(goal, report_to, kind, self._thread.id, pr)
            if schedule and isinstance(ctx.deps, Scheduled):
                schedule.started(ctx.deps.job.id, task.id)
            return f"Started task #{task.id}."

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
        async def installed_apps(on: Literal["phone", "pc"] = "phone") -> str:
            """The apps installed on the phone, or the PC."""
            try:
                return ", ".join(await devices[on].apps())
            except DeviceError as error:
                return f"Couldn't ask the {devices[on].label}: {error}"

        @self._agent.tool
        async def owner_location(ctx: RunContext[Event]) -> str:
            """Where the owner is, for "near me" or a pickup (the phone is at home): where they last shared, how long
            ago, and a link that shares it now in one tap. Text them the link when you need it fresh; it comes back
            as a notice."""
            last = whereabouts.latest()
            known = f"Last shared {ago(last.time, time.time())}: {last}." if last else "Never shared."
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
            if public and not ctx.deps.owner_asked:
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

        if schedule:

            @self._agent.tool
            async def do_later(
                ctx: RunContext[Event],
                what: str,
                at: str | None = None,
                in_minutes: float | None = None,
                every: str | None = None,
                report_via: str | None = None,
                max_runs: int = DEFAULT_RUNS,
            ) -> str:
                """Do something later, when the owner asks: "in 15 minutes get me an Uber home", "at 20:30 text me to
                call Sam", "tomorrow morning ring me with the weather". When: `at`, their local time ("2026-11-18
                08:00"), or `in_minutes`. Repeating ("every weekday at 7"): `every`, cron in their time ("0 7 * * 1-5"),
                with `at` only if it starts later. `what`: all you'll need then, as it'll read then ("today's weather",
                not "tomorrow's"), e.g. "Start a phone task: book an Uber home"; you're woken with it, in this thread.
                Replies go where you're talking now; `report_via` as for start_task ("voice" rings them).
                A repeat stops after `max_runs` (default 50, at most 200: each run costs a model call); size it to
                the need. "Check every 30 minutes until it changes": repeat, and cancel_later it yourself when it has."""
                report_to = ctx.deps.reply_to
                if report_via and not (report_to := gateway.owner(report_via)):
                    return no_channel(report_via)
                now = time.time()
                try:
                    due = parse(at, in_minutes, every, now)
                except ValueError as error:
                    return str(error)
                said = ctx.deps.message.text if isinstance(ctx.deps, OwnerMessage) else ""
                # Said, not refused: the same thing set up twice fired twice in the same minute.
                twins = [j.id for j in schedule.upcoming() if j.thread == self._thread.id and _same(j.what, what)]
                job = schedule.add(due, what, said, report_to, self._thread.id, every or "", max_runs)
                repeats = f", then {every} (cron), at most {job.max_runs} runs" if every else ""
                done = f"Scheduled #{job.id} for {when(due, now)}{repeats}, via {report_to.channel}."
                if twins:
                    done += f" {', '.join(f'#{id}' for id in twins)} here already does the same: cancel_later one?"
                return done

            @self._agent.tool
            async def cancel_later(ctx: RunContext[Event], id: int) -> str:
                """Cancel something scheduled, all its repeats too. The owner can cancel any; on your own, only what
                was scheduled in this thread (a repeat you're woken by, once it has done its job). Then tell them."""
                job = schedule.get(id)
                if job and not ctx.deps.from_owner and job.thread != self._thread.id:
                    return f"#{id} was scheduled in another conversation: only the owner can cancel it."
                if not job or not schedule.cancel(id):
                    return f"Nothing scheduled as #{id} (it has run, or was cancelled)."
                if isinstance(ctx.deps, Scheduled) and ctx.deps.job.id == id:
                    self._stopped = True  # it ends itself: this run's words are its one report
                return f"Cancelled #{id}" + (f": no more of its {job.every} repeats." if job.every else ".")

            @self._agent.tool
            async def note_progress(ctx: RunContext[Event], progress: str) -> str:
                """In a scheduled run: set the line the owner's panel shows beside it, live ("Still 3 of 7, next check
                13:45"). Not a message: nothing is sent. Use it as a long run goes, and for a repeat's no-change runs."""
                if not isinstance(ctx.deps, Scheduled):
                    return "Only a scheduled run has an entry to note progress on."
                schedule.note(ctx.deps.job.id, progress[:300])
                return "Noted on the panel."

        if coder:

            @self._agent.tool
            async def improve(
                ctx: RunContext[Event], request: str, pr: int | None = None, report_via: str | None = None
            ) -> str:
                """Change Archangel itself, when the owner asks for a new ability or a fix ("add a Telegram channel"),
                or ask why it does something: Claude Code works on its code (the archangel repo on GitHub, README
                included; never a device) in the background (often 10-20 minutes)
                and opens a PR for the owner to merge, or answers. `pr`: carry on that PR instead ("no, make it
                shorter"). The repo is public: write `request` with nothing about the owner's life (no names, places,
                numbers, accounts); say what to build or find out. `report_via` as for start_task."""
                if not ctx.deps.owner_asked:
                    return "Only the owner can ask for changes to Archangel. Ask them."
                return start(ctx, request, report_via, "code", pr)

            @self._agent.tool
            async def merge(ctx: RunContext[Event], pr: int) -> str:
                """Merge an improvement's PR, which deploys it (Archangel restarts): only when the owner says so."""
                if not ctx.deps.owner_asked:
                    return "Only the owner can merge a change. Ask them."
                try:
                    merged = await coder.merge(pr)
                except Exception as error:  # GitHub; the conversation goes on
                    log.exception("Merge failed")
                    return f"Couldn't merge it: {error}"
                # The deploy restarts Archangel: the owner should know what it cuts off.
                running = [t for t in self._tasks.list() if t.status in (Status.QUEUED, Status.RUNNING)]
                cut = [f"#{t.id} ({t.kind}): {t.goal}" for t in running if t.kind != "code"]
                kept = [f"#{t.id}: {t.goal}" for t in running if t.kind == "code"]
                if cut:
                    merged += "\nRunning now, so cut off by the deploy (reported interrupted):\n" + "\n".join(cut)
                if kept:
                    merged += "\nCode tasks running now; their GitHub runs carry on, and are picked up after:\n"
                    merged += "\n".join(kept)
                return merged

        @self._agent.tool_plain
        async def hang_up() -> str:
            """On a call, hang up once your reply is said: they've said bye, or asked you to."""
            self._ends = True
            return "It hangs up after your reply."

        @self._agent.tool
        async def send(ctx: RunContext[Event], channel: str, text: str, screenshot: bool = False) -> str:
            """Message the owner on another channel, e.g. results they asked to get by email.

            Your reply goes where you're talking now regardless; use this only for elsewhere. `screenshot` attaches
            the screen you were just shown, e.g. texting it to the owner while on a call. Write it as
            `writing_style` says for that channel: the style above is for where you're talking now.
            """
            if not (to := gateway.owner(channel)):
                return no_channel(channel)
            if channel == ctx.deps.reply_to.channel and not screenshot:  # or the owner gets it twice
                return f"Your reply already goes via {channel}: say it there."
            images = ctx.deps.attachments() if screenshot else ()
            if screenshot and not images:
                return "There's no screenshot to send."
            await gateway.send(Message(to, (Text(text), *images), thread=self._thread.id))
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

    async def wake(self, job: Job) -> None:
        """Something scheduled is due: handled with the task its previous run started, to carry on from."""
        await self.handle(Scheduled(job, self._store.task(job.last_task) if job.last_task else None))

    async def handle(self, event: Event) -> None:
        async with self._lock:
            with activity.acting("orchestrator"):
                # Both model calls, so side by side: which thread it's in, and what the twin knows that bears on it.
                route, found = await asyncio.gather(self._threads.route(event), self._hear(event))
                self._thread = thread = route.thread
                if isinstance(event, OwnerMessage) and event.line:
                    self._store.file_line(event.line, thread.id)
                # Taken once, not before each model call: rebuilt after start_task, the list shows the new task as if
                # it had been there all along, and the model apologises for starting a duplicate.
                self._arrived_to = await self._situation(thread)
                if found:
                    self._arrived_to += (
                        "\n\nFrom the context twin (what's known about the owner, not what you can do), on what "
                        f"the owner just said:\n{found}"
                    )
                if self._styles:  # the reply goes to the owner, on the channel it goes back by
                    self._arrived_to += "\n\n" + self._styles.guidance(Audience(True, via=event.reply_to.channel))
                self._ends = False
                self._stopped = False
                try:
                    messages = self._threads.history(thread)
                    log.debug(
                        "in thread #%d (%d prior messages): %s", thread.id, len(messages), describe(event.prompt())
                    )
                    result = await logs.run(self._agent, prompt(event.prompt()), deps=event, message_history=messages)
                    self._threads.save(route, result.all_messages())
                    log.debug("out to %s: %s", event.reply_to, result.output)
                    parts = (Text(result.output), *event.attachments()) if result.output else ()
                except Exception as error:
                    log.exception("Failed to handle: %s", describe(event.prompt()))
                    parts = (Text(event.fallback(error)),)
                # A repeat waiting for something reports once: when it ends (the condition met, or its last run). Any
                # other run is silent, whatever the model wrote, so a "no change yet" never reaches the owner.
                if isinstance(event, Scheduled) and event.job.every and not event.job.last and not self._stopped:
                    parts = ()
                reply = Message(event.reply_to, parts, answering=event.from_owner, ends=self._ends, thread=thread.id)
                await self._gateway.send(reply)

    async def _hear(self, event: Event) -> str:
        """What the twin knows that bears on what the owner said: answered knowing them, not just their top page."""
        if not (event.from_owner and self._twin):
            return ""
        return await self._twin.hear(" ".join(part.text for part in event.prompt() if isinstance(part, Text)))

    async def _situation(self, thread: Thread) -> str:
        tasks = self._tasks.list()[::-1]
        shown = [t for t in tasks if t.status.open] + [t for t in tasks if not t.status.open][-FINISHED_TASKS_SHOWN:]
        lines = [
            f"#{t.id} [{t.status}] {'' if t.kind == 'phone' else t.kind + ': '}{t.goal}"
            + (f" → {t.report[:300]}" if t.report else "")
            + (" (report public)" if t.public else "")
            + (f" (thread #{t.thread})" if t.thread and t.thread != thread.id else "")
            for t in shown
        ]
        about = []
        if self._twin:  # only the top page: the model took it for all it knew, and said it didn't know their mum
            about.append(
                "The top page of the owner's context twin; far more is behind its links. Ask the twin (ask_twin) "
                f"before saying you don't know something about them:\n{self._twin.root()}"
            )
        scheduled = self._schedule and self._schedule.describe()
        quick = self._quick and self._quick.describe()
        return "\n".join(
            [
                f"Now: {datetime.now(TIMEZONE):%A %-d %B %Y, %H:%M}, the owner's time.",
                *about,
                self._threads.describe(thread),
                *([f"Quick tools, live data answered in seconds (try these first): {quick}."] if quick else []),
                f"Devices: {await self._describe_devices()}.",
                f"Channels the owner can be reached on: {self._gateway.describe()}.",
                "Connected services, by profile: "
                + (self._connectors.describe() or "none (added on the panel, under Connectors)")
                + ".",
                "Tasks when this arrived (open, then recently finished):"
                if lines
                else "Tasks when this arrived: none.",
                *lines,
                *(["Scheduled (do_later):", scheduled] if scheduled else []),
            ]
        )

    async def _describe_devices(self) -> str:
        """Each device that has ever connected, and whether it's online now."""
        states = await asyncio.gather(*(device.state() for device in self._devices.values()))
        devices = zip(self._devices.values(), states, strict=True)
        found = [f"{device.label} ({state})" for device, state in devices if state]
        return "; ".join(found) or "none connected yet"
