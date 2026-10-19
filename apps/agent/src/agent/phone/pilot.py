"""The phone sub-agent: carries out one task by looking at the screen, acting, and checking the result."""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

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

from agent import activity, logs
from agent.history import forget_old_screenshots
from agent.messages import Image
from agent.models import Instructions, content
from agent.phone.guard import Guard
from agent.phone.investigator import Investigator
from agent.phone.relay import Phone, PhoneError
from agent.phone.vision import Vision
from agent.runs import Answer, Outcome, Record, answered, question, results

log = logging.getLogger(__name__)

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()
MAX_STEPS = 120
"""Only for a runaway: the pilot itself judges when it's stuck, going in circles, or should stop."""
RECENT_STEPS = 12
"""How many recent steps the investigator reads."""
SCREENS_INVESTIGATED = 3
TOOL_RETRIES = 3
SETTLE_SECONDS = 1.0
SCROLLS = {
    "down": (0.5, 0.7, 0.5, 0.3),
    "up": (0.5, 0.3, 0.5, 0.7),
    "right": (0.8, 0.5, 0.2, 0.5),
    "left": (0.2, 0.5, 0.8, 0.5),
}
UNLOCK = (0.5, 0.85, 0.5, 0.25)  # swipe up from below the lock icon, clear of the navigation bar


@dataclass(frozen=True)
class Kit:
    """What a pilot works with: the phone, and the models that find things on it, keep it safe and unstick it."""

    phone: Phone
    vision: Vision
    guard: Guard
    investigator: Investigator


@dataclass
class _Session:
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
    moved: bool = False
    """The phone's GPS is faked, until the run ends."""

    async def shoot(self) -> Image:
        self.screen = await self.kit.phone.screenshot()
        self.screens.append(self.screen)
        return self.screen

    async def act(self, action: Awaitable[None], note: str) -> ToolReturn | str:
        """Run `action`, then show the model the result. Failures go back to the model to work around."""
        try:
            await action
        except PhoneError as error:
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

    The phone then goes to the home screen; set `leave_open` only if the task asked for the screen to stay as it is.
    """
    return _Ending(True, report, leave_open)


def give_up(reason: str) -> _Ending:
    """Stop without finishing: say what you did and what is in the way."""
    return _Ending(False, reason)


class Pilot:
    """Carries out one task on the phone: look, act, check, repeat.

    Pauses, resumably, when the owner must answer a question or approve a consequential tap.
    """

    def __init__(self, model: Model, kit: Kit, instructions: Instructions = INSTRUCTIONS):
        self._kit = kit
        self._agent = Agent(
            model,
            name="pilot",
            instructions=instructions,
            deps_type=_Session,
            output_type=[ToolOutput(done, name="done"), ToolOutput(give_up, name="give_up"), DeferredToolRequests],
            retries=TOOL_RETRIES,
            # One action at a time: each must be seen to land before the next.
            model_settings=ModelSettings(parallel_tool_calls=False),
            tools=[
                open_app,
                close_app,
                Tool(tap, args_validator=_check_tap),
                type_text,
                scroll,
                Tool(press, args_validator=_check_press),
                look,
                set_location,
                second_opinion,
                ask_owner,
            ],
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
        log.info("Phone task: %s", goal)
        session = _Session(
            self._kit, goal, record, inbox=[] if inbox is None else inbox, said=[] if said is None else said
        )
        try:
            return await self._run(session, paused, answer)
        finally:
            if session.moved:  # paused too: another task may have the phone meanwhile
                with contextlib.suppress(PhoneError):
                    await self._kit.phone.location()

    async def _run(self, session: _Session, paused: pydantic_ai.Conversation | None, answer: Answer | None) -> Outcome:
        goal, record = session.goal, session.record
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
        except PhoneError as error:
            log.warning("Phone unavailable: %s", error)
            return Outcome("failed", f"Couldn't use the phone: {error}", screen=session.screen)
        except UsageLimitExceeded:
            log.warning("Gave up after %d steps", MAX_STEPS)
            return await _stuck(session, f"Gave up after {MAX_STEPS} steps without finishing.")
        except Exception as error:
            # Hand the failure back to decide what to do, rather than ending the conversation.
            log.exception("Phone task failed")
            return Outcome("failed", f"Failed unexpectedly ({type(error).__name__}: {error}).", screen=session.screen)

        output = result.output
        if isinstance(output, DeferredToolRequests):
            log.info("Waiting on the owner: %s", question(output))
            status = "needs_approval" if output.approvals else "needs_answer"
            return Outcome(status, question(output), result.conversation, session.screen)
        log.debug("Phone task result: %s", output)
        if output.finished and not output.leave_open:
            with contextlib.suppress(PhoneError):  # tidying up; the task's done either way
                await self._kit.phone.key("home")
        return Outcome("done" if output.finished else "gave_up", output.report, screen=session.screen)


async def _stuck(session: _Session, report: str) -> Outcome:
    # Whoever decides what next (the orchestrator, then the owner) needs to know why, not just that it stopped.
    if diagnosis := await session.diagnose():
        report += f"\nWhat seems to be in the way: {diagnosis}"
    return Outcome("stuck", report, screen=session.screen)


async def open_app(ctx: RunContext[_Session], name: str) -> ToolReturn | str:
    """Open an installed app by its name, e.g. "WhatsApp"."""
    return await _with_app(ctx, name, ctx.deps.kit.phone.launch, f"Opened {name}.")


async def close_app(ctx: RunContext[_Session], name: str) -> ToolReturn | str:
    """Close an app completely (as swiping it away in recents does), so it starts afresh next time."""
    return await _with_app(ctx, name, ctx.deps.kit.phone.close, f"Closed {name}.")


async def _with_app(
    ctx: RunContext[_Session], name: str, action: Callable[[str], Awaitable[None]], note: str
) -> ToolReturn | str:
    apps = await ctx.deps.kit.phone.apps()
    package = next((package for label, package in apps.items() if label.lower() == name.lower()), None)
    if not package:
        return ctx.deps.failed(f"No app called {name!r}. Installed: {', '.join(apps)}.")
    return await ctx.deps.act(action(package), note)


async def tap(ctx: RunContext[_Session], target: str, hold: bool = False) -> ToolReturn | str:
    """Tap something on the current screen. Describe it so it can be found, e.g. "the Send button".

    Set `hold` to long-press.
    """
    try:
        with activity.call("vision", "agent", target) as reply:
            box = await ctx.deps.kit.vision.find(ctx.deps.screen, target)
            reply(box or "not found")
    except Exception as error:  # vision is remote models: any failure is one failed step, not a failed task
        return ctx.deps.failed(f"Couldn't search the screen: {error}")
    log.debug("located %r: %s", target, box)
    if not box:
        return ctx.deps.failed(f"Couldn't find {target} on the screen.")
    return await ctx.deps.act(ctx.deps.kit.phone.tap(*box.center, hold=hold), f"Tapped {target}.")


async def type_text(ctx: RunContext[_Session], text: str) -> ToolReturn | str:
    """Replace the text in the focused input field. Tap the field first."""
    return await ctx.deps.act(ctx.deps.kit.phone.type(text), "Typed.")


async def scroll(ctx: RunContext[_Session], direction: Literal["down", "up", "left", "right"]) -> ToolReturn | str:
    """Scroll to reveal more content in `direction`. `up` drags the finger down (on a lock screen, the shade)."""
    return await ctx.deps.act(ctx.deps.kit.phone.swipe(*SCROLLS[direction]), f"Scrolled {direction}.")


async def press(
    ctx: RunContext[_Session], key: Literal["back", "home", "recents", "notifications", "enter", "unlock"]
) -> ToolReturn | str:
    """Press a system button. `enter` submits the focused field (search, next, send); `unlock` swipes away a lock
    screen without a PIN (apps opened while locked are behind it)."""
    phone = ctx.deps.kit.phone
    pressing = phone.swipe(*UNLOCK) if key == "unlock" else phone.key(key)
    return await ctx.deps.act(pressing, f"Pressed {key}.")


async def look(ctx: RunContext[_Session], reason: str = "") -> ToolReturn:
    """Take a fresh screenshot, e.g. while something loads."""
    # `reason` because models like to say why, and a refused call three times over ends the task.
    return await ctx.deps.look(f"Current screen{f' ({reason})' if reason else ''}.")


async def set_location(ctx: RunContext[_Session], lat: float, lon: float) -> ToolReturn | str:
    """Make the phone's GPS say it's at `lat`, `lon`, for an app that only uses where the phone is ("near me").
    Not where the app takes an address or area: type that. Lasts until the task ends or pauses."""
    ctx.deps.moved = True
    return await ctx.deps.act(ctx.deps.kit.phone.location(lat, lon), f"The phone's GPS now says {lat}, {lon}.")


async def second_opinion(ctx: RunContext[_Session], question: str = "") -> str:
    """Ask a fresh model, which reads your recent screens and steps, what's in the way and how past it."""
    advice = await ctx.deps.diagnose(question)
    ctx.deps.record(f"Second opinion: {advice or 'none'}", None)
    return advice or "No opinion: decide for yourself."


async def ask_owner(ctx: RunContext[_Session], question: str) -> str:
    """Ask the phone's owner something only they can answer, e.g. which of two contacts, or a code they received.

    The task pauses until they reply. Don't use this for permission: consequential taps are checked automatically.
    """
    raise CallDeferred(metadata={"question": question})


async def _check_tap(ctx: RunContext[_Session], target: str, hold: bool = False) -> None:
    await _check(ctx, f"tap {target}")


async def _check_press(ctx: RunContext[_Session], key: str) -> None:
    if key == "enter":
        await _check(ctx, "press Enter in the focused field")


async def _check(ctx: RunContext[_Session], action: str) -> None:
    """Pause for the owner's approval if the guard says `action` commits something. Runs before the tool."""
    if ctx.tool_call_approved:
        return
    with activity.call("guard", "agent", action) as reply:
        consequence = await ctx.deps.kit.guard.check(ctx.deps.screen, action, ctx.deps.goal, ctx.deps.said)
        reply(f"needs approval: {consequence}" if consequence else "fine")
    if consequence:
        raise ApprovalRequired(metadata={"action": consequence})
