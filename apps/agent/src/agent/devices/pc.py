"""The PC's pilot: its tools and prompt. Mouse and keyboard, and its lock screen."""

from pathlib import Path
from typing import Literal

from pydantic_ai import RunContext, Tool, ToolReturn

from agent.devices.pilot import Platform, Session, ask_owner, check, look, open_app, second_opinion

INSTRUCTIONS = (Path(__file__).parent / "pc.md").read_text()
WHEEL_NOTCHES = 5
SCROLLS = {
    "down": (0, WHEEL_NOTCHES),
    "up": (0, -WHEEL_NOTCHES),
    "right": (WHEEL_NOTCHES, 0),
    "left": (-WHEEL_NOTCHES, 0),
}
UNCHECKED = {
    "esc", "escape", "tab", "shift+tab", "up", "down", "left", "right", "pageup", "pagedown", "home", "end",
    "ctrl+a", "ctrl+c", "ctrl+f", "ctrl+l", "ctrl+t", "alt+tab", "win", "backspace",
}  # fmt: skip
"""Keys that only move around or select: everything else (Enter, Delete, ctrl+s, …) is checked like a click."""


async def click(
    ctx: RunContext[Session], target: str, button: Literal["left", "right"] = "left", double: bool = False
) -> ToolReturn | str:
    """Click something on the current screen. Describe it so it can be found, e.g. "the Send button".

    `button` right for a context menu; `double` to open a file or folder.
    """
    box = await ctx.deps.find(target)
    if isinstance(box, str):
        return box
    action = ctx.deps.device.click(*box.center, button=button, double=double)
    return await ctx.deps.act(action, f"{_verb(button, double).capitalize()}ed {target}.")


def _verb(button: str, double: bool) -> str:
    return "right-click" if button == "right" else "double-click" if double else "click"


async def type_text(ctx: RunContext[Session], text: str) -> ToolReturn | str:
    """Type at the cursor, as the keyboard would. It doesn't clear what's there: select it first (press ctrl+a).
    Click the field first. Line breaks start a new line; they never submit."""
    return await ctx.deps.act(ctx.deps.device.type(text), "Typed.")


async def scroll(
    ctx: RunContext[Session], direction: Literal["down", "up", "left", "right"], within: str = ""
) -> ToolReturn | str:
    """Scroll to reveal more content in `direction`, with the mouse wheel: over `within` (e.g. "the message list")
    when there's more than one scrolling area, else the middle of the screen."""
    x, y = 0.5, 0.5
    if within:
        box = await ctx.deps.find(within)
        if isinstance(box, str):
            return box
        x, y = box.center
    dx, dy = SCROLLS[direction]
    return await ctx.deps.act(ctx.deps.device.scroll(x, y, dx, dy), f"Scrolled {direction}.")


async def press(ctx: RunContext[Session], keys: str) -> ToolReturn | str:
    """Press a key or a chord: "enter", "esc", "ctrl+l" (a browser's address bar), "alt+tab", "alt+f4" (closes the
    window in front), "win" (Start)."""
    return await ctx.deps.act(ctx.deps.device.key(keys), f"Pressed {keys}.")


async def unlock(ctx: RunContext[Session]) -> ToolReturn | str:
    """Unlock the PC's lock screen with its PIN (kept on the PC; you never see it). It's locked again afterwards."""
    pc = ctx.deps.device
    ctx.deps.undo["lock"] = lambda: pc.key("lock")
    return await ctx.deps.act(pc.unlock(), "Unlocked.")


async def _check_click(ctx: RunContext[Session], target: str, button: str = "left", double: bool = False) -> None:
    await check(ctx, f"{_verb(button, double)} {target}")


async def _check_press(ctx: RunContext[Session], keys: str) -> None:
    if keys.lower().replace(" ", "") not in UNCHECKED:
        await check(ctx, f"press {keys}")


PC = Platform(
    "pc",
    INSTRUCTIONS,
    [
        open_app,
        Tool(click, args_validator=_check_click),
        type_text,
        scroll,
        Tool(press, args_validator=_check_press),
        look,
        unlock,
        second_opinion,
        ask_owner,
    ],
)
