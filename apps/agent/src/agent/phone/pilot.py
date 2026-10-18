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
    DeferredToolResults,
    ModelSettings,
    RunContext,
    Tool,
    ToolDenied,
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

log = logging.getLogger(__name__)

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()
MAX_STEPS = 40
WRAP_UP_STEPS = 5
"""With this many steps left, the pilot is told to finish with what it has, rather than lose it all."""
MAX_FAILURES = 5
RECENT_STEPS = 6
LOOP_REPEATS = 3
"""The same step this many times in the last RECENT_STEPS means the pilot is going round in circles."""
MAX_SECOND_OPINIONS = 2
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
class Answer:
    """The owner's reply to a paused task: free text, and for an approval, yes or no."""

    text: str
    approved: bool = False


@dataclass(frozen=True)
class Outcome:
    status: Literal["done", "gave_up", "stuck", "failed", "needs_approval", "needs_answer"]
    report: str
    """What happened; when it needs the owner, what they're being asked."""
    run: pydantic_ai.Conversation | None = None
    """Set when it needs the owner: pass back to `Pilot.run` with their answer to continue."""
    screen: Image | None = None


@dataclass(frozen=True)
class Kit:
    """What a pilot works with: the phone, and the models that find things on it, keep it safe and unstick it."""

    phone: Phone
    vision: Vision
    guard: Guard
    investigator: Investigator


Record = Callable[[str, Image | None], None]
"""Hears each step of a run as it happens, with the screen it left, for watching the task live."""


class Stuck(Exception):
    pass


@dataclass
class _Session:
    kit: Kit
    goal: str
    record: Record
    screen: Image | None = None
    failures: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    screens: deque[Image] = field(default_factory=lambda: deque(maxlen=SCREENS_INVESTIGATED))
    second_opinions: int = 0
    taken: int = 0  # steps, for the wrap-up warning (`steps` is cleared for second opinions)

    async def shoot(self) -> Image:
        self.screen = await self.kit.phone.screenshot()
        self.screens.append(self.screen)
        return self.screen

    async def act(self, action: Awaitable[None], note: str) -> ToolReturn | str:
        """Run `action`, then show the model the result. Failures go back to the model to work around."""
        try:
            await action
        except PhoneError as error:
            return await self.failed(f"Failed: {error}")
        log.info(note)
        self.failures.clear()
        return await self.look(note)

    async def failed(self, problem: str) -> str:
        """Report a failed action, nudging the model off a repeated mistake and ending runs that are going nowhere."""
        log.info(problem)
        self.failures.append(problem)
        self.steps.append(problem)
        self.taken += 1
        if len(self.failures) >= MAX_FAILURES:
            self.record(problem, None)
            raise Stuck(f"{len(self.failures)} actions in a row failed. The last: {problem}")
        if len(self.failures) >= 2 and self.failures[-1] == self.failures[-2]:
            nudge = "That failed the same way twice. Try something different, ask_owner, or give_up."
            problem = f"{problem}\n{nudge}{await self.second_opinion()}"
        self.record(problem, None)
        return problem

    async def look(self, note: str) -> ToolReturn:
        """Let the screen settle, then show it to the model with `note`."""
        await asyncio.sleep(SETTLE_SECONDS)
        await self.shoot()
        self.steps.append(note)
        if self.looping(note):
            note += await self.second_opinion()
        self.taken += 1
        if (left := MAX_STEPS - self.taken) <= WRAP_UP_STEPS:
            note += f"\n{left} steps left: finish now with done (with what you've found) or give_up."
        self.record(note, self.screen)
        return ToolReturn(note, content=[content(self.screen)])

    def looping(self, note: str) -> bool:
        # Scrolling the same way several times is how lists get searched, not a loop.
        return not note.startswith("Scrolled") and self.steps[-RECENT_STEPS:].count(note) >= LOOP_REPEATS

    async def second_opinion(self) -> str:
        """Advice from the investigator for a pilot going in circles, a few times per run at most."""
        if self.second_opinions >= MAX_SECOND_OPINIONS:
            return ""
        self.second_opinions += 1
        advice = await self.diagnose()
        self.steps.clear()  # a fresh start: only a new loop earns another opinion
        return f"\nSecond opinion, from a model that looked at the recent screens: {advice}" if advice else ""

    async def diagnose(self) -> str | None:
        with activity.call("investigator", "agent", self.goal) as reply:
            advice = await self.kit.investigator.investigate(
                self.goal, self.steps[-RECENT_STEPS * 2 :], list(self.screens)
            )
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
                ask_owner,
            ],
            capabilities=[ProcessHistory(forget_old_screenshots)],
        )

    async def run(
        self, goal: str, record: Record, paused: pydantic_ai.Conversation | None = None, answer: Answer | None = None
    ) -> Outcome:
        """Start `goal`, or continue a `paused` run with the owner's `answer`. Each step goes to `record`."""
        log.info("Phone task: %s", goal)
        session = _Session(self._kit, goal, record)
        try:
            # Fresh on resume too: an approved tap must find its target on the screen as it is now.
            screen = await session.shoot()
            record(f"Continuing. {_answered(answer)}" if paused else "Started.", screen)
            if paused:
                prompt, resume = None, {"conversation": paused, "deferred_tool_results": _results(paused, answer)}
            else:
                prompt, resume = [goal, content(screen)], {}
            result = await logs.run(
                self._agent, prompt, deps=session, usage_limits=UsageLimits(request_limit=MAX_STEPS), **resume
            )
        except PhoneError as error:
            log.warning("Phone unavailable: %s", error)
            return Outcome("failed", f"Couldn't use the phone: {error}", screen=session.screen)
        except Stuck as error:
            log.warning("Stuck: %s", error)
            return await _stuck(session, f"Stopped, stuck: {error}")
        except UsageLimitExceeded:
            log.warning("Gave up after %d steps", MAX_STEPS)
            return await _stuck(session, f"Gave up after {MAX_STEPS} steps without finishing.")
        except Exception as error:
            # Hand the failure back to decide what to do, rather than ending the conversation.
            log.exception("Phone task failed")
            return Outcome("failed", f"Failed unexpectedly ({type(error).__name__}: {error}).", screen=session.screen)

        output = result.output
        if isinstance(output, DeferredToolRequests):
            log.info("Waiting on the owner: %s", _question(output))
            status = "needs_approval" if output.approvals else "needs_answer"
            return Outcome(status, _question(output), result.conversation, session.screen)
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


def _answered(answer: Answer | None) -> str:
    if not answer:
        return ""
    return ("Approved" if answer.approved else "Owner replied") + (f": {answer.text}" if answer.text else ".")


def _question(requests: DeferredToolRequests) -> str:
    asks = [f"Approve: {requests.metadata[call.tool_call_id]['action']}?" for call in requests.approvals]
    asks += [requests.metadata[call.tool_call_id]["question"] for call in requests.calls]
    return "\n".join(asks)


def _results(paused: pydantic_ai.Conversation, answer: Answer | None) -> DeferredToolResults:
    requests = paused.deferred_tool_requests or DeferredToolRequests()
    answer = answer or Answer("")
    no = ToolDenied(f"The owner said no: {answer.text}" if answer.text else "The owner said no.")
    return requests.build_results(
        approvals={call.tool_call_id: answer.approved or no for call in requests.approvals},
        calls={call.tool_call_id: f"The owner replied: {answer.text}" for call in requests.calls},
    )


async def open_app(ctx: RunContext[_Session], name: str) -> ToolReturn | str:
    """Open an installed app by its name, e.g. "WhatsApp"."""
    apps = await ctx.deps.kit.phone.apps()
    if not (package := _package(apps, name)):
        return await ctx.deps.failed(f"No app called {name!r}. Installed: {', '.join(apps)}.")
    return await ctx.deps.act(ctx.deps.kit.phone.launch(package), f"Opened {name}.")


async def close_app(ctx: RunContext[_Session], name: str) -> ToolReturn | str:
    """Close an app completely (as swiping it away in recents does), so it starts afresh next time."""
    apps = await ctx.deps.kit.phone.apps()
    if not (package := _package(apps, name)):
        return await ctx.deps.failed(f"No app called {name!r}. Installed: {', '.join(apps)}.")
    return await ctx.deps.act(ctx.deps.kit.phone.close(package), f"Closed {name}.")


def _package(apps: dict[str, str], name: str) -> str | None:
    return next((package for label, package in apps.items() if label.lower() == name.lower()), None)


async def tap(ctx: RunContext[_Session], target: str, hold: bool = False) -> ToolReturn | str:
    """Tap something on the current screen. Describe it so it can be found, e.g. "the Send button".

    Set `hold` to long-press.
    """
    try:
        with activity.call("vision", "agent", target) as reply:
            box = await ctx.deps.kit.vision.find(ctx.deps.screen, target)
            reply(box or "not found")
    except Exception as error:  # vision is remote models: any failure is one failed step, not a failed task
        return await ctx.deps.failed(f"Couldn't search the screen: {error}")
    log.debug("located %r: %s", target, box)
    if not box:
        return await ctx.deps.failed(f"Couldn't find {target} on the screen.")
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
        consequence = await ctx.deps.kit.guard.check(ctx.deps.screen, action, ctx.deps.goal)
        reply(f"needs approval: {consequence}" if consequence else "fine")
    if consequence:
        raise ApprovalRequired(metadata={"action": consequence})
