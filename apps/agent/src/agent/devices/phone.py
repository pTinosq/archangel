"""The phone's pilot: its tools and prompt. Taps, swipes and Android's buttons."""

import asyncio
from pathlib import Path
from typing import Literal

from pydantic_ai import RunContext, Tool, ToolReturn

from agent.devices.pilot import Platform, Session, ask_owner, check, look, open_app, second_opinion, with_app
from agent.devices.relay import Device

INSTRUCTIONS = (Path(__file__).parent / "phone.md").read_text()
CLOSE_PAUSE_SECONDS = 1.0  # for an app to open, and recents to animate in
SCROLLS = {
    "down": (0.5, 0.7, 0.5, 0.3),
    "up": (0.5, 0.3, 0.5, 0.7),
    "right": (0.8, 0.5, 0.2, 0.5),
    "left": (0.2, 0.5, 0.8, 0.5),
}
UNLOCK = (0.5, 0.85, 0.5, 0.25)  # swipe up from below the lock icon, clear of the navigation bar


async def close(phone: Device, package: str) -> None:
    """Close an app for good, as swiping it away in recents does, so it starts afresh next time."""
    await phone.launch(package)
    await asyncio.sleep(CLOSE_PAUSE_SECONDS)
    await phone.key("recents")  # its card, frontmost, is in the middle of the screen
    await asyncio.sleep(CLOSE_PAUSE_SECONDS)
    await phone.swipe(0.5, 0.5, 0.5, 0.02)
    await asyncio.sleep(CLOSE_PAUSE_SECONDS / 2)
    await phone.key("home")


async def close_app(ctx: RunContext[Session], name: str) -> ToolReturn | str:
    """Close an app completely (as swiping it away in recents does), so it starts afresh next time."""
    return await with_app(ctx, name, lambda package: close(ctx.deps.device, package), f"Closed {name}.")


async def tap(ctx: RunContext[Session], target: str, hold: bool = False) -> ToolReturn | str:
    """Tap something on the current screen. Describe it so it can be found, e.g. "the Send button".

    Set `hold` to long-press.
    """
    box = await ctx.deps.find(target)
    if isinstance(box, str):
        return box
    return await ctx.deps.act(ctx.deps.device.tap(*box.center, hold=hold), f"Tapped {target}.")


async def type_text(ctx: RunContext[Session], text: str) -> ToolReturn | str:
    """Replace the text in the focused input field. Tap the field first."""
    return await ctx.deps.act(ctx.deps.device.type(text), "Typed.")


async def scroll(ctx: RunContext[Session], direction: Literal["down", "up", "left", "right"]) -> ToolReturn | str:
    """Scroll to reveal more content in `direction`. `up` drags the finger down (on a lock screen, the shade)."""
    return await ctx.deps.act(ctx.deps.device.swipe(*SCROLLS[direction]), f"Scrolled {direction}.")


async def press(
    ctx: RunContext[Session], key: Literal["back", "home", "recents", "notifications", "enter", "unlock"]
) -> ToolReturn | str:
    """Press a system button. `enter` submits the focused field (search, next, send); `unlock` swipes away a lock
    screen without a PIN (apps opened while locked are behind it)."""
    phone = ctx.deps.device
    pressing = phone.swipe(*UNLOCK) if key == "unlock" else phone.key(key)
    return await ctx.deps.act(pressing, f"Pressed {key}.")


async def set_location(ctx: RunContext[Session], lat: float, lon: float) -> ToolReturn | str:
    """Make the phone's GPS say it's at `lat`, `lon`, for an app that only uses where the phone is ("near me").
    Not where the app takes an address or area: type that. Lasts until the task ends or pauses."""
    phone = ctx.deps.device
    ctx.deps.undo["location"] = phone.location
    return await ctx.deps.act(phone.location(lat, lon), f"The phone's GPS now says {lat}, {lon}.")


async def _check_tap(ctx: RunContext[Session], target: str, hold: bool = False) -> None:
    await check(ctx, f"tap {target}")


async def _check_press(ctx: RunContext[Session], key: str) -> None:
    if key == "enter":
        await check(ctx, "press Enter in the focused field")


async def _home(phone: Device) -> None:
    await phone.key("home")


PHONE = Platform(
    "phone",
    INSTRUCTIONS,
    [
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
    finish=_home,
)
