"""The PC's pilot: its tools and prompt. Mouse and keyboard, its lock screen, and PowerShell."""

from pathlib import Path
from typing import Literal

from pydantic_ai import RunContext, Tool, ToolReturn

from agent.devices.pilot import Platform, Session, ask_owner, check, look, open_app, second_opinion
from agent.devices.relay import DeviceError

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
OUTPUT_SHOWN = 4000
"""Characters of a command's output (and of its errors) the pilot sees: enough to read; more would fill its context."""


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


async def run_command(ctx: RunContext[Session], command: str) -> str:
    """Run PowerShell on the PC as its signed-in user, in their home folder, and get what it printed. Stopped after
    about 10 s, with all it started. E.g. `Start-Process https://example.com`, `Start-Process notepad`,
    `Invoke-Item Downloads`, `Get-ChildItem Documents -Recurse -Filter *.pdf | Select -First 20 FullName`."""
    try:
        ran = await ctx.deps.device.powershell(command)
    except DeviceError as error:
        return ctx.deps.failed(f"Failed: {error}")
    code = ran["exitCode"]
    result = "Stopped: still running after the time limit." if code is None else f"Exit code {code}."
    for heading, text in (("Output", ran["stdout"].strip()), ("Errors", ran["stderr"].strip())):
        if text:
            cut = f"\n… ({len(text) - OUTPUT_SHOWN} more characters)" if len(text) > OUTPUT_SHOWN else ""
            result += f"\n{heading}:\n{text[:OUTPUT_SHOWN]}{cut}"
    ctx.deps.steps.append(f"Ran `{command}`: {result.splitlines()[0]}")
    ctx.deps.record(f"Ran `{command}`. {result}", None)
    return result


async def _check_command(ctx: RunContext[Session], command: str) -> None:
    await check(ctx, f"run this PowerShell command on the PC: {command}", command=command)


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
        Tool(run_command, args_validator=_check_command),
        second_opinion,
        ask_owner,
    ],
)
