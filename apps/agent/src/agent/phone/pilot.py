import asyncio
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
    BinaryContent,
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

from agent import logs
from agent.history import forget_old_screenshots
from agent.phone.guard import Guard
from agent.phone.investigator import Investigator
from agent.phone.relay import Phone, PhoneError, Screenshot
from agent.phone.vision import Locator

log = logging.getLogger(__name__)

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()
MAX_STEPS = 40
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


@dataclass(frozen=True)
class Answer:
    """The owner's reply to a paused task: free text, and for an approval, yes or no."""

    text: str
    approved: bool = False


@dataclass(frozen=True)
class Outcome:
    status: Literal["done", "gave_up", "stuck", "failed", "waiting"]
    report: str
    """What happened; when waiting, what the owner is being asked."""
    run: pydantic_ai.Conversation | None = None
    """Set when waiting: pass back to `Pilot.run` with the owner's answer to continue."""
    screen: Screenshot | None = None


class Stuck(Exception):
    pass


Record = Callable[[str, Screenshot | None], None]
"""Hears each step of a run as it happens, with the screen it left, for watching the task live."""


@dataclass
class _Session:
    phone: Phone
    locator: Locator
    guard: Guard
    investigator: Investigator
    goal: str
    record: Record = lambda text, screen: None
    screen: Screenshot | None = None
    failures: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    screens: deque[Screenshot] = field(default_factory=lambda: deque(maxlen=SCREENS_INVESTIGATED))
    second_opinions: int = 0

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
        self.screen = await self.phone.screenshot()
        self.screens.append(self.screen)
        log.debug("screenshot %s", logs.save_screenshot(self.screen.jpeg))
        self.steps.append(note)
        if self.looping(note):
            note += await self.second_opinion()
        self.record(note, self.screen)
        return ToolReturn(note, content=[_image(self.screen)])

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
        return await self.investigator.investigate(self.goal, self.steps[-RECENT_STEPS * 2 :], list(self.screens))


@dataclass(frozen=True)
class _Ending:
    finished: bool
    report: str


def done(report: str) -> _Ending:
    """Finish: the task is complete. `report` says what you did and anything the task asked you to find out."""
    return _Ending(True, report)


def give_up(reason: str) -> _Ending:
    """Stop without finishing: say what you did and what is in the way."""
    return _Ending(False, reason)


class Pilot:
    """Carries out one task on the phone: look, act, check, repeat.

    Pauses, resumably, when the owner must answer a question or approve a consequential tap.
    """

    def __init__(
        self,
        model: Model,
        phone: Phone,
        locator: Locator,
        guard: Guard,
        investigator: Investigator,
        instructions: str | Callable[[], str] = INSTRUCTIONS,
    ):
        self._phone = phone
        self._locator = locator
        self._guard = guard
        self._investigator = investigator
        self._agent = Agent(
            model,
            instructions=instructions,
            deps_type=_Session,
            output_type=[ToolOutput(done, name="done"), ToolOutput(give_up, name="give_up"), DeferredToolRequests],
            retries=TOOL_RETRIES,
            # One action at a time: each must be seen to land before the next.
            model_settings=ModelSettings(parallel_tool_calls=False),
            tools=[
                open_app,
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
        self,
        goal: str,
        paused: pydantic_ai.Conversation | None = None,
        answer: Answer | None = None,
        record: Record | None = None,
    ) -> Outcome:
        """Start `goal`, or continue a `paused` run with the owner's `answer`."""
        log.info("Phone task: %s", goal)
        session = _Session(self._phone, self._locator, self._guard, self._investigator, goal)
        if record:
            session.record = record
        try:
            # Fresh on resume too: an approved tap must find its target on the screen as it is now.
            session.screen = await self._phone.screenshot()
            session.screens.append(session.screen)
            log.debug("screenshot %s", logs.save_screenshot(session.screen.jpeg))
            session.record(f"Continuing. {_answered(answer)}" if paused else "Started.", session.screen)
            if paused:
                prompt, resume = None, dict(conversation=paused, deferred_tool_results=_results(paused, answer))
            else:
                prompt, resume = [goal, _image(session.screen)], {}
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
            return Outcome("waiting", _question(output), result.conversation, session.screen)
        log.debug("Phone task result: %s", output)
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
    apps = await ctx.deps.phone.apps()
    package = next((p for label, p in apps.items() if label.lower() == name.lower()), None)
    if not package:
        return await ctx.deps.failed(f"No app called {name!r}. Installed: {', '.join(apps)}.")
    return await ctx.deps.act(ctx.deps.phone.launch(package), f"Opened {name}.")


async def tap(ctx: RunContext[_Session], target: str, hold: bool = False) -> ToolReturn | str:
    """Tap something on the current screen. Describe it so it can be found, e.g. "the Send button".

    Set `hold` to long-press.
    """
    try:
        box = await ctx.deps.locator.locate(ctx.deps.screen, target)
    except Exception as error:  # the locators are remote models: any failure is one failed step, not a failed task
        return await ctx.deps.failed(f"Couldn't search the screen: {error}")
    log.debug("located %r: %s", target, box)
    if not box:
        return await ctx.deps.failed(f"Couldn't find {target} on the screen.")
    return await ctx.deps.act(ctx.deps.phone.tap(*box.center, hold=hold), f"Tapped {target}.")


async def type_text(ctx: RunContext[_Session], text: str) -> ToolReturn | str:
    """Replace the text in the focused input field. Tap the field first."""
    return await ctx.deps.act(ctx.deps.phone.type(text), "Typed.")


async def scroll(ctx: RunContext[_Session], direction: Literal["down", "up", "left", "right"]) -> ToolReturn | str:
    """Scroll to reveal more content in `direction`."""
    return await ctx.deps.act(ctx.deps.phone.swipe(*SCROLLS[direction]), f"Scrolled {direction}.")


async def press(
    ctx: RunContext[_Session], key: Literal["back", "home", "recents", "notifications", "enter"]
) -> ToolReturn | str:
    """Press a system button. `enter` submits the focused field (search, next, send)."""
    return await ctx.deps.act(ctx.deps.phone.key(key), f"Pressed {key}.")


async def look(ctx: RunContext[_Session]) -> ToolReturn:
    """Take a fresh screenshot, e.g. while something loads."""
    return await ctx.deps.look("Current screen.")


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
    if consequence := await ctx.deps.guard.check(ctx.deps.screen, action, ctx.deps.goal):
        raise ApprovalRequired(metadata={"action": consequence})


def _image(screen: Screenshot) -> BinaryContent:
    return BinaryContent(data=screen.jpeg, media_type="image/jpeg")
