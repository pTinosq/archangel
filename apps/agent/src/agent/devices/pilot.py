"""The device sub-agent: carries out one task on a device (the phone, the PC) by looking at the screen, acting, and
checking the result. What's shared lives here; each platform (`phone.py`, `pc.py`) brings its tools and prompt."""

import asyncio
import contextlib
import difflib
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

import pydantic_ai
from pydantic_ai import (
    Agent,
    ApprovalRequired,
    CallDeferred,
    DeferredToolRequests,
    ModelSettings,
    RunContext,
    Tool,
    ToolOutput,
    ToolReturn,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models import Model
from pydantic_ai.toolsets import AbstractToolset

from agent import activity, logs
from agent.devices.guard import Guard
from agent.devices.investigator import Investigator
from agent.devices.relay import Device, DeviceError
from agent.devices.vision import Box, Vision
from agent.history import forget_old_screenshots
from agent.messages import Image
from agent.models import Instructions, content
from agent.runs import Answer, Outcome, Record, answered, question, results

log = logging.getLogger(__name__)

MAX_STEPS = 120
"""Only for a runaway: the pilot itself judges when it's stuck, going in circles, or should stop."""
RECENT_STEPS = 12
"""How many recent steps the investigator reads."""
SCREENS_INVESTIGATED = 3
TOOL_RETRIES = 3
SETTLE_SECONDS = 1.0


@dataclass(frozen=True)
class Kit:
    """What a pilot works with: the device, and the models that find things on it, keep it safe and unstick it."""

    device: Device
    vision: Vision
    guard: Guard
    investigator: Investigator


@dataclass(frozen=True)
class Platform:
    """What differs between devices: the pilot's tools and prompt, and tidying up after a finished task."""

    name: str
    instructions: str
    tools: Sequence[Tool | Callable]
    finish: Callable[[Device], Awaitable[None]] | None = None
    """After a task is done, unless it asked to leave the screen as it is: the phone goes home."""


@dataclass
class Session:
    kit: Kit
    goal: str
    record: Record
    screen: Image | None = None
    steps: list[str] = field(default_factory=list)
    screens: deque[Image] = field(default_factory=lambda: deque(maxlen=SCREENS_INVESTIGATED))
    inbox: list[str] = field(default_factory=list)
    """The owner's word while it runs (the task's own list, appended to from outside)."""
    said: list[str] = field(default_factory=list)
    """All the owner has said about the task, for the guard: what they've agreed to isn't asked again."""
    undo: dict[str, Callable[[], Awaitable[None]]] = field(default_factory=dict)
    """What to put back when the run ends or pauses (another task may have the device meanwhile): a faked location,
    an unlocked PC."""

    @property
    def device(self) -> Device:
        return self.kit.device

    async def shoot(self) -> Image:
        self.screen = await self.device.screenshot()
        self.screens.append(self.screen)
        return self.screen

    async def act(self, action: Awaitable[None], note: str) -> ToolReturn | str:
        """Run `action`, then show the model the result. Failures go back to the model to work around."""
        try:
            await action
        except DeviceError as error:
            return self.failed(f"Failed: {error}")
        log.info(note)
        return await self.look(note)

    def failed(self, problem: str) -> str:
        """Report a failed action back to the model, to work around."""
        log.info(problem)
        self.steps.append(problem)
        self.record(problem, None)
        return problem

    async def look(self, note: str) -> ToolReturn:
        """Let the screen settle, then show it to the model with `note`."""
        await asyncio.sleep(SETTLE_SECONDS)
        await self.shoot()
        self.steps.append(note)
        self.record(note, self.screen)
        if self.inbox:
            note += "".join(f"\nThe owner says, meanwhile: {said}" for said in self.inbox)
            self.inbox.clear()
        return ToolReturn(note, content=[content(self.screen)])

    async def find(self, target: str) -> Box | str:
        """Where `target` is on the current screen, or what went wrong (already reported back)."""
        try:
            with activity.call("vision", "agent", target) as reply:
                box = await self.kit.vision.find(self.screen, target)
                reply(box or "not found")
        except Exception as error:  # vision is remote models: any failure is one failed step, not a failed task
            return self.failed(f"Couldn't search the screen: {error}")
        log.debug("located %r: %s", target, box)
        return box or self.failed(f"Couldn't find {target} on the screen.")

    async def diagnose(self, question: str = "") -> str | None:
        goal = f"{self.goal}\nThe pilot asks: {question}" if question else self.goal
        with activity.call("investigator", "agent", goal) as reply:
            advice = await self.kit.investigator.investigate(goal, self.steps[-RECENT_STEPS:], list(self.screens))
            reply(advice or "no opinion")
        return advice


@dataclass(frozen=True)
class _Ending:
    finished: bool
    report: str
    leave_open: bool = False


def done(report: str, leave_open: bool = False) -> _Ending:
    """Finish: the task is complete. `report` says what you did and anything the task asked you to find out.

    A phone then goes to its home screen; set `leave_open` only if the task asked for the screen to stay as it is.
    """
    return _Ending(True, report, leave_open)


def give_up(reason: str) -> _Ending:
    """Stop without finishing: say what you did and what is in the way."""
    return _Ending(False, reason)


class Pilot:
    """Carries out one task on a device: look, act, check, repeat.

    Pauses, resumably, when the owner must answer a question or approve a consequential action.
    """

    def __init__(
        self,
        model: Model,
        kit: Kit,
        platform: Platform,
        instructions: Instructions | None = None,
        toolsets: Sequence[AbstractToolset] = (),
    ):
        """`toolsets`: more tools beside the device's, e.g. asking the context twin."""
        self._kit = kit
        self._platform = platform
        self._agent = Agent(
            model,
            name=f"{platform.name} pilot",
            toolsets=toolsets,
            instructions=instructions or platform.instructions,
            deps_type=Session,
            output_type=[ToolOutput(done, name="done"), ToolOutput(give_up, name="give_up"), DeferredToolRequests],
            retries=TOOL_RETRIES,
            # One action at a time: each must be seen to land before the next.
            model_settings=ModelSettings(parallel_tool_calls=False),
            tools=platform.tools,
            capabilities=[ProcessHistory(forget_old_screenshots)],
        )

    async def run(
        self,
        goal: str,
        record: Record,
        paused: pydantic_ai.Conversation | None = None,
        answer: Answer | None = None,
        inbox: list[str] | None = None,
        said: list[str] | None = None,
    ) -> Outcome:
        """Start `goal`, or continue a `paused` run with the owner's `answer`. Each step goes to `record`; what the
        owner says meanwhile is appended to `inbox`; `said` holds all they've said about it (both kept up by the caller)."""
        log.info("%s task: %s", self._platform.name.capitalize(), goal)
        session = Session(
            self._kit, goal, record, inbox=[] if inbox is None else inbox, said=[] if said is None else said
        )
        try:
            return await self._run(session, paused, answer)
        finally:
            for put_back in session.undo.values():
                with contextlib.suppress(DeviceError):
                    await put_back()

    async def _run(self, session: Session, paused: pydantic_ai.Conversation | None, answer: Answer | None) -> Outcome:
        goal, record, label = session.goal, session.record, self._kit.device.label
        try:
            # Fresh on resume too: an approved tap must find its target on the screen as it is now.
            screen = await session.shoot()
            record(f"Continuing. {answered(answer)}" if paused else "Started.", screen)
            if paused:
                prompt, resume = None, {"conversation": paused, "deferred_tool_results": results(paused, answer)}
            else:
                prompt, resume = [goal, content(screen)], {}
            result = await logs.run(
                self._agent, prompt, deps=session, usage_limits=UsageLimits(request_limit=MAX_STEPS), **resume
            )
        except DeviceError as error:
            log.warning("%s unavailable: %s", label, error)
            return Outcome("failed", f"Couldn't use the {label}: {error}", screen=session.screen)
        except UsageLimitExceeded:
            log.warning("Gave up after %d steps", MAX_STEPS)
            return await _stuck(session, f"Gave up after {MAX_STEPS} steps without finishing.")
        except Exception as error:
            # Hand the failure back to decide what to do, rather than ending the conversation.
            log.exception("%s task failed", label)
            return Outcome("failed", f"Failed unexpectedly ({type(error).__name__}: {error}).", screen=session.screen)

        output = result.output
        if isinstance(output, DeferredToolRequests):
            log.info("Waiting on the owner: %s", question(output))
            status = "needs_approval" if output.approvals else "needs_answer"
            return Outcome(status, question(output), result.conversation, session.screen)
        log.debug("%s task result: %s", label, output)
        if output.finished and not output.leave_open and self._platform.finish:
            with contextlib.suppress(DeviceError):  # tidying up; the task's done either way
                await self._platform.finish(self._kit.device)
        return Outcome("done" if output.finished else "gave_up", output.report, screen=session.screen)


async def _stuck(session: Session, report: str) -> Outcome:
    # Whoever decides what next (the orchestrator, then the owner) needs to know why, not just that it stopped.
    if diagnosis := await session.diagnose():
        report += f"\nWhat seems to be in the way: {diagnosis}"
    return Outcome("stuck", report, screen=session.screen)


# Tools every device has


async def open_app(ctx: RunContext[Session], name: str) -> ToolReturn | str:
    """Open an installed app by its name, e.g. "WhatsApp"."""
    return await with_app(ctx, name, ctx.deps.device.launch, f"Opened {name}.")


async def with_app(
    ctx: RunContext[Session], name: str, action: Callable[[str], Awaitable[None]], note: str
) -> ToolReturn | str:
    apps = {label.lower(): (label, package) for label, package in (await ctx.deps.device.apps()).items()}
    # Models say "Chrome" for "Google Chrome": a name inside only one app's is that app.
    found = apps.get(name.lower()) or _only([app for key, app in apps.items() if name.lower() in key])
    if not found:
        # The nearest few, not all: a PC has hundreds, and the list would fill the pilot's context.
        nearest = difflib.get_close_matches(name.lower(), apps, n=5, cutoff=0)
        return ctx.deps.failed(f"No app called {name!r}. Nearest: {', '.join(apps[key][0] for key in nearest)}.")
    return await ctx.deps.act(action(found[1]), note)


def _only[T](items: list[T]) -> T | None:
    return items[0] if len(items) == 1 else None


async def look(ctx: RunContext[Session], reason: str = "") -> ToolReturn:
    """Take a fresh screenshot, e.g. while something loads."""
    # `reason` because models like to say why, and a refused call three times over ends the task.
    return await ctx.deps.look(f"Current screen{f' ({reason})' if reason else ''}.")


async def second_opinion(ctx: RunContext[Session], question: str = "") -> str:
    """Ask a fresh model, which reads your recent screens and steps, what's in the way and how past it."""
    advice = await ctx.deps.diagnose(question)
    ctx.deps.record(f"Second opinion: {advice or 'none'}", None)
    return advice or "No opinion: decide for yourself."


async def ask_owner(ctx: RunContext[Session], question: str) -> str:
    """Ask the owner something only they can answer, e.g. which of two contacts, or a code they received.

    The task pauses until they reply. Don't use this for permission: consequential actions are checked automatically.
    """
    raise CallDeferred(metadata={"question": question})


async def check(ctx: RunContext[Session], action: str, command: str = "") -> None:
    """Pause for the owner's approval if the guard says `action` commits something. Runs before the tool.

    A `command` is judged without the screen, and shown to the owner as it is."""
    if ctx.tool_call_approved:
        return
    screen = None if command else ctx.deps.screen
    with activity.call("guard", "agent", action) as reply:
        consequence = await ctx.deps.kit.guard.check(screen, action, ctx.deps.goal, ctx.deps.said)
        reply(f"needs approval: {consequence}" if consequence else "fine")
    if consequence:
        raise ApprovalRequired(metadata={"action": f"{consequence}, running `{command}`" if command else consequence})
