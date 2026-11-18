"""The PC itself: what each command does on Windows."""

import asyncio
import io
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from bridge import keys, uia, win32
from bridge.device import CommandError, Ran, Screen, Shot
from bridge.protocol import ErrorCode, Message

log = logging.getLogger(__name__)

APPS_KEPT_SECONDS = 60
"""Listing the Start menu takes PowerShell a second or two; launch checks against the list each time."""
CLICK_GAP_SECONDS = 0.05
"""Between a double-click's clicks: well within Windows' double-click time (500 ms by default)."""
DRAG_STEP_SECONDS = 0.01
WHEEL_GAP_SECONDS = 0.02
CURTAIN_SECONDS = 1.5
"""For the lock screen's picture to slide away, showing the PIN box."""
SIGN_IN_SECONDS = 6.0
COMMAND_SECONDS = 10.0
"""How long a powershell command may run: inside the relay's 15 s, so the caller hears how it ended."""
LOCKED = (
    "The PC is locked, and only the bridge's service can reach the lock screen. "
    "Install the service from the bridge's settings to have it unlocked."
)


class WindowsDevice:
    """Screen and input work on one thread, kept on the desktop that has input (the lock screen included, under the
    service); UI Automation on another, since it sets COM up per thread and a thread with COM windows can't switch
    desktops."""

    def __init__(self, secret: str = ""):
        self._secret = secret
        self._desktop = win32.Desktop()
        self._input = ThreadPoolExecutor(1, "desktop")
        self._uia = ThreadPoolExecutor(1, "uia")
        self._system = win32.is_system()
        self._session = win32.session()
        self._apps: tuple[float, list[Message]] = (0.0, [])
        self._mac = _mac()

    def screen(self) -> Screen:
        width, height = win32.screen_size()
        return Screen(width, height, win32.density())

    def mac(self) -> str | None:
        return self._mac

    async def screenshot(self, scale: float, quality: int) -> Shot:
        return await self._on_desktop(self._screenshot, scale, quality)

    async def tap(self, x: int, y: int, duration_ms: int, button: str, count: int) -> None:
        await self._on_desktop(self._tap, x, y, duration_ms, button, count)

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        await self._on_desktop(self._swipe, x1, y1, x2, y2, duration_ms)

    async def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        await self._on_desktop(self._scroll, x, y, dx, dy)

    async def type(self, text: str) -> None:
        await self._on_desktop(self._type, text)

    async def key(self, key: str) -> None:
        if key.strip().lower() == keys.LOCK:
            await asyncio.to_thread(win32.run_as_user, ["rundll32.exe", "user32.dll,LockWorkStation"])
            return
        await self._on_desktop(self._key, keys.chord(key))

    async def launch(self, app: str) -> None:
        if not any(found["package"] == app for found in await self.apps()):
            raise CommandError(ErrorCode.NOT_FOUND, f"No app with id {app!r}")
        # explorer opens Start menu entries of every kind (desktop apps, Store apps, shortcuts) by their id.
        await asyncio.to_thread(win32.run_as_user, ["explorer.exe", f"shell:AppsFolder\\{app}"])

    async def apps(self) -> list[Message]:
        listed, apps = self._apps
        if time.monotonic() - listed < APPS_KEPT_SECONDS:
            return apps
        script = "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
        output = (await asyncio.to_thread(win32.powershell, script)).stdout
        try:
            found = json.loads(output or "[]")
        except ValueError as error:
            raise CommandError(ErrorCode.FAILED, f"Couldn't list the apps: {output.strip()[:200]}") from error
        found = [found] if isinstance(found, dict) else found
        apps = sorted(
            ({"label": app["Name"], "package": app["AppID"]} for app in found if app.get("Name") and app.get("AppID")),
            key=lambda app: app["label"].lower(),
        )
        self._apps = time.monotonic(), apps
        return apps

    async def ui_tree(self) -> list[Message]:
        return await asyncio.get_running_loop().run_in_executor(self._uia, uia.tree)

    async def unlock(self) -> None:
        await self._on_desktop(self._unlock)

    async def powershell(self, script: str) -> Ran:
        # Not on the desktop thread: it needs no input, and runs as the user while the PC is locked too.
        return await asyncio.to_thread(win32.powershell, script, COMMAND_SECONDS)

    # On the desktop thread

    async def _on_desktop(self, work, *args):
        return await asyncio.get_running_loop().run_in_executor(self._input, self._attached, work, args)

    def _attached(self, work, args):
        if self._desktop.follow() is None:
            raise CommandError(ErrorCode.FAILED, LOCKED)
        return work(*args)

    def _screenshot(self, scale: float, quality: int) -> Shot:
        image = win32.capture()
        if scale < 1:
            size = max(1, round(image.width * scale)), max(1, round(image.height * scale))
            image = image.resize(size, Image.Resampling.LANCZOS)
        jpeg = io.BytesIO()
        image.save(jpeg, "JPEG", quality=quality)
        return Shot(image.width, image.height, jpeg.getvalue())

    def _tap(self, x: int, y: int, duration_ms: int, button: str, count: int) -> None:
        win32.move(x, y)
        for click in range(count):
            if click:
                time.sleep(CLICK_GAP_SECONDS)
            win32.button(button, True)
            time.sleep(duration_ms / 1000)
            win32.button(button, False)

    def _swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        win32.move(x1, y1)
        win32.button("left", True)
        try:
            steps = max(1, round(duration_ms / 1000 / DRAG_STEP_SECONDS))
            for step in range(1, steps + 1):
                time.sleep(DRAG_STEP_SECONDS)
                win32.move(round(x1 + (x2 - x1) * step / steps), round(y1 + (y2 - y1) * step / steps))
        finally:
            win32.button("left", False)

    def _scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        win32.move(x, y)
        # A notch at a time: some apps take one big turn as one small one.
        for notch, horizontal in [(-1 if dy > 0 else 1, False)] * abs(dy) + [(1 if dx > 0 else -1, True)] * abs(dx):
            win32.wheel(notch, horizontal)  # the wheel turned towards you scrolls down: negative
            time.sleep(WHEEL_GAP_SECONDS)

    def _type(self, text: str) -> None:
        for i, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
            if i:
                win32.press(win32.VK_RETURN, [win32.VK_SHIFT])  # a new line that never submits
            for j, part in enumerate(line.split("\t")):
                if j:
                    win32.press(win32.VK_TAB)
                if part:
                    win32.unicode(part)

    def _key(self, held: list[keys.Key]) -> None:
        *modifiers, last = held
        down = [(modifier.vk, modifier.extended) for modifier in modifiers]
        vk, extended = last.vk, last.extended
        if vk is None:  # punctuation: where it is depends on the layout
            if not (found := win32.layout_key(last.char)):
                raise CommandError(ErrorCode.INVALID_COMMAND, f"No {last.char!r} key on this keyboard layout")
            vk, needs = found
            down += [(code, False) for code in needs if code not in (code for code, _ in down)]
        pressed = []
        try:
            for code, wide in down:
                win32.key(code, True, wide)
                pressed.append((code, wide))
            win32.key(vk, True, extended)
            win32.key(vk, False, extended)
        finally:
            for code, wide in reversed(pressed):  # never leave a modifier held down
                win32.key(code, False, wide)

    def _unlock(self) -> None:
        if not win32.lock_screen_up(self._session):
            if self._desktop.name.lower() == "default":
                return  # not locked
            raise CommandError(ErrorCode.FAILED, "That's an administrator prompt, not the lock screen")
        if not self._secret:
            raise CommandError(ErrorCode.FAILED, "No PIN or password is set in the bridge's settings")
        width, height = win32.screen_size()
        self._tap(width // 2, height // 2, 50, "left", 1)  # lifts the curtain, showing the PIN box
        time.sleep(CURTAIN_SECONDS)
        self._key(keys.chord("ctrl+a"))  # anything typed there before is replaced
        for char in self._secret:
            # Keys rather than characters: the sign-in box reads keys. A character no key types goes as itself.
            if found := win32.layout_key(char):
                win32.press(*found)
            else:
                win32.unicode(char)
        # A PIN signs in as its last digit goes in; a password needs Enter. Never Enter once unlocked: it would land
        # in whatever window is in front.
        if self._signed_in(CURTAIN_SECONDS):
            return
        win32.press(win32.VK_RETURN)
        if not self._signed_in(SIGN_IN_SECONDS):
            raise CommandError(
                ErrorCode.FAILED, "Still locked after typing the PIN: it may be wrong, or the lock screen wasn't ready"
            )

    def _signed_in(self, within: float) -> bool:
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if not win32.lock_screen_up(self._session) and self._desktop.follow() == "Default":
                return True
        return False


def _mac() -> str | None:
    """The card on the way to the internet, wired before wireless (Wake-on-LAN over Wi-Fi rarely works)."""
    script = (
        "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | "
        "Sort-Object { $_.NetAdapter.PhysicalMediaType -ne '802.3' } | Select-Object -First 1 | "
        "ForEach-Object { $_.NetAdapter.MacAddress }"
    )
    try:
        found = win32.powershell(script, as_user=False).stdout.strip()
    except Exception:
        log.warning("Couldn't find the network card's address; the PC can't be woken", exc_info=True)
        return None
    parts = found.replace("-", ":").lower().split(":")
    return ":".join(parts) if len(parts) == 6 and all(len(part) == 2 for part in parts) else None
