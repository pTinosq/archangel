import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import httpx
from pydantic_ai import (
    Agent,
    BinaryContent,
    ModelMessage,
    ModelRequest,
    RunContext,
    ToolReturn,
    UsageLimitExceeded,
    UsageLimits,
    UserPromptPart,
)
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models import Model

from agent.phone.relay import Phone, PhoneError, Screenshot
from agent.phone.vision import Locator

log = logging.getLogger(__name__)

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()
MAX_STEPS = 40
SETTLE_SECONDS = 1.0
KEPT_SCREENSHOTS = 2
SCROLLS = {
    "down": (0.5, 0.7, 0.5, 0.3),
    "up": (0.5, 0.3, 0.5, 0.7),
    "right": (0.8, 0.5, 0.2, 0.5),
    "left": (0.2, 0.5, 0.8, 0.5),
}


@dataclass
class _Session:
    phone: Phone
    locator: Locator
    screen: Screenshot | None = None

    async def act(self, action: Awaitable[None], note: str) -> ToolReturn | str:
        """Run `action`, then show the model the result. Failures go back to the model to work around."""
        try:
            await action
        except PhoneError as error:
            log.info("%s failed: %s", note, error)
            return f"Failed: {error}"
        log.info(note)
        return await self.look(note)

    async def look(self, note: str) -> ToolReturn:
        """Let the screen settle, then show it to the model with `note`."""
        await asyncio.sleep(SETTLE_SECONDS)
        self.screen = await self.phone.screenshot()
        return ToolReturn(note, content=[_image(self.screen)])


class Pilot:
    """Carries out one task on the phone: look, act, check, repeat. Returns what happened."""

    def __init__(self, model: Model, phone: Phone, locator: Locator):
        self._phone = phone
        self._locator = locator
        self._agent = Agent(
            model,
            instructions=INSTRUCTIONS,
            deps_type=_Session,
            tools=[open_app, tap, type_text, scroll, press, look],
            capabilities=[ProcessHistory(forget_old_screenshots)],
        )

    async def run(self, task: str) -> str:
        log.info("Phone task: %s", task)
        session = _Session(self._phone, self._locator)
        try:
            session.screen = await self._phone.screenshot()
            result = await self._agent.run(
                [task, _image(session.screen)],
                deps=session,
                usage_limits=UsageLimits(request_limit=MAX_STEPS),
            )
        except PhoneError as error:
            return f"Couldn't use the phone: {error}"
        except UsageLimitExceeded:
            return f"Gave up after {MAX_STEPS} steps without finishing."
        return result.output


async def open_app(ctx: RunContext[_Session], name: str) -> ToolReturn | str:
    """Open an installed app by its name, e.g. "WhatsApp"."""
    apps = await ctx.deps.phone.apps()
    package = next((p for label, p in apps.items() if label.lower() == name.lower()), None)
    if not package:
        return f"No app called {name!r}. Installed: {', '.join(apps)}."
    return await ctx.deps.act(ctx.deps.phone.launch(package), f"Opened {name}.")


async def tap(ctx: RunContext[_Session], target: str, hold: bool = False) -> ToolReturn | str:
    """Tap something on the current screen. Describe it so it can be found, e.g. "the Send button".

    Set `hold` to long-press.
    """
    try:
        box = await ctx.deps.locator.locate(ctx.deps.screen, target)
    except httpx.HTTPError as error:
        return f"Couldn't search the screen: {error}"
    if not box:
        log.info("Couldn't find %s", target)
        return f"Couldn't find {target} on the screen."
    return await ctx.deps.act(ctx.deps.phone.tap(*box.center, hold=hold), f"Tapped {target}.")


async def type_text(ctx: RunContext[_Session], text: str) -> ToolReturn | str:
    """Replace the text in the focused input field. Tap the field first."""
    return await ctx.deps.act(ctx.deps.phone.type(text), "Typed.")


async def scroll(ctx: RunContext[_Session], direction: Literal["down", "up", "left", "right"]) -> ToolReturn | str:
    """Scroll to reveal more content in `direction`."""
    return await ctx.deps.act(ctx.deps.phone.swipe(*SCROLLS[direction]), f"Scrolled {direction}.")


async def press(
    ctx: RunContext[_Session], key: Literal["back", "home", "recents", "notifications"]
) -> ToolReturn | str:
    """Press a system button."""
    return await ctx.deps.act(ctx.deps.phone.key(key), f"Pressed {key}.")


async def look(ctx: RunContext[_Session]) -> ToolReturn:
    """Take a fresh screenshot, e.g. while something loads."""
    return await ctx.deps.look("Current screen.")


def forget_old_screenshots(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Keep only the latest screenshots in context; older ones cost tokens and add nothing."""
    kept = 0
    trimmed: list[ModelMessage] = []
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            parts = []
            for part in message.parts:
                if isinstance(part, UserPromptPart) and _has_image(part):
                    kept += 1
                    if kept > KEPT_SCREENSHOTS:
                        part = replace(part, content=[_drop_image(item) for item in part.content])
                parts.append(part)
            message = replace(message, parts=parts)
        trimmed.append(message)
    return trimmed[::-1]


def _image(screen: Screenshot) -> BinaryContent:
    return BinaryContent(data=screen.jpeg, media_type="image/jpeg")


def _has_image(part: UserPromptPart) -> bool:
    return not isinstance(part.content, str) and any(isinstance(item, BinaryContent) for item in part.content)


def _drop_image(item):
    return "(earlier screenshot)" if isinstance(item, BinaryContent) else item
