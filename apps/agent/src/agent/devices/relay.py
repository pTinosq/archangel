import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import httpx

from agent import activity
from agent.messages import Image

log = logging.getLogger(__name__)

LABELS = {"android": "phone", "windows": "PC"}
PHONE_SCREENSHOT = (
    0.5,
    30,
)  # scale, quality: half the bytes of 80; Jev and Luna locate just as well (tested on Settings)
PC_SCREENSHOT_WIDTH = 1600
"""A desktop's text is small: shrunk to about this wide, not halved (a 4K screen would still be 1920 wide)."""
PC_SCREENSHOT_QUALITY = 60
OFFLINE_WAIT_SECONDS = 60
WAKE_WAIT_SECONDS = 150
"""A PC woken from sleep needs to resume, rejoin the network and dial the relay."""
WAKE_EVERY_SECONDS = 30
POLL_SECONDS = 2
READ_ONLY = {"screenshot", "apps", "ui_tree"}


class DeviceError(Exception):
    """The device couldn't do what was asked. The message is safe to show the model."""


class Memory(Protocol):
    """Where a device's latest hello is kept, for when it's offline: a PC asleep is woken by the address it gave."""

    def save_device(self, hello: dict[str, Any]) -> None: ...
    def last_device(self, platform: str) -> dict[str, Any] | None: ...


class Device:
    """A device (the phone, the PC), reached through the relay. Positions are fractions of the screen, from the
    top-left.

    It's whichever device of its `platform` is connected; `names` choose only when several are.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        platform: str = "android",
        names: Sequence[str] = (),
        memory: Memory | None = None,
        wake: Callable[[str], Awaitable[None]] | None = None,
    ):
        """`wake`: sends a Wake-on-LAN packet for a network card's address (the phone does, on the home network)."""
        self._client = client
        self.platform = platform
        self.label = LABELS.get(platform, platform)
        self._names = set(names)
        self._memory = memory
        self._wake = wake
        self._found: str | None = None  # the connected device's name
        self._size: tuple[int, int] | None = None  # in pixels, as of the latest screenshot
        self._remembered: dict[str, Any] | None = None

    async def screenshot(self) -> Image:
        scale, quality = await self._screenshot_size()
        data = await self._command("screenshot", scale=scale, quality=quality)
        # Taps are placed on this screen, so they use its size: it changes when the phone rotates.
        self._size = round(data["width"] / scale), round(data["height"] / scale)
        return Image(base64.b64decode(data["jpeg"]), "image/jpeg")

    async def tap(self, x: float, y: float, hold: bool = False) -> None:
        px, py = await self._pixels(x, y)
        await self._command("tap", x=px, y=py, durationMs=800 if hold else 50)

    async def click(self, x: float, y: float, button: str = "left", double: bool = False) -> None:
        """A mouse click (a PC's tap)."""
        px, py = await self._pixels(x, y)
        await self._command("tap", x=px, y=py, button=button, count=2 if double else 1)

    async def swipe(self, x1: float, y1: float, x2: float, y2: float) -> None:
        start, end = await self._pixels(x1, y1), await self._pixels(x2, y2)
        await self._command("swipe", x1=start[0], y1=start[1], x2=end[0], y2=end[1])

    async def scroll(self, x: float, y: float, dx: int = 0, dy: int = 0) -> None:
        """The mouse wheel at `x`, `y`, in notches; positive reveals what's right or below. A PC's."""
        px, py = await self._pixels(x, y)
        await self._command("scroll", x=px, y=py, dx=dx, dy=dy)

    async def type(self, text: str) -> None:
        await self._command("type", text=text)

    async def key(self, key: str) -> None:
        await self._command("key", key=key)

    async def launch(self, package: str) -> None:
        await self._command("launch", package=package)

    async def unlock(self) -> None:
        """Unlock the PC with the PIN its bridge keeps."""
        await self._command("unlock")

    async def location(self, lat: float | None = None, lon: float | None = None) -> None:
        """Fake GPS at `lat`, `lon`, for apps that only use where the phone is. Neither: where it really is."""
        await self._command("location", **({} if lat is None else {"lat": lat, "lon": lon}))

    async def wake(self, mac: str) -> None:
        """Broadcast a Wake-on-LAN packet on the phone's network, for the card at `mac`."""
        await self._command("wake", mac=mac)

    async def apps(self) -> dict[str, str]:
        """Launchable apps, label → package (or, on a PC, its Start menu id)."""
        data = await self._command("apps")
        return {app["label"]: app["package"] for app in data["apps"]}

    async def state(self) -> str | None:
        """For the orchestrator: online, offline (and whether it'll be woken), or None if it's never connected."""
        try:
            if await self._hello():
                return "online"
        except DeviceError as error:
            return str(error)
        except httpx.HTTPError:
            return "unknown (the relay is unreachable)"
        if not (known := self._last()):
            return None
        return "offline (it's woken when a task needs it)" if self._mac(known) else "offline"

    async def _screenshot_size(self) -> tuple[float, int]:
        if self.platform != "windows":
            return PHONE_SCREENSHOT
        width = self._size[0] if self._size else (await self._screen())[0]
        return min(1.0, PC_SCREENSHOT_WIDTH / width), PC_SCREENSHOT_QUALITY

    async def _pixels(self, x: float, y: float) -> tuple[int, int]:
        width, height = self._size or await self._screen()
        return round(x * width), round(y * height)

    async def _screen(self) -> tuple[int, int]:
        """The size it said hello with, until a screenshot says otherwise."""
        if not (hello := await self._reachable_hello()):
            await self._wait_online()
            if not (hello := await self._reachable_hello()):  # gone again already
                raise DeviceError(f"The {self.label} is not connected")
        self._size = hello["screen"]["width"], hello["screen"]["height"]
        return self._size

    async def _name(self) -> str:
        if not self._found and not await self._reachable_hello():
            await self._wait_online()
        return self._found

    async def _hello(self) -> dict[str, Any] | None:
        """Its hello (name, screen), if it's connected."""
        devices = (await self._client.get("/devices")).json()
        mine = [hello for hello in devices if hello.get("platform", "android") == self.platform]
        if len(mine) > 1 and self._names:
            mine = [hello for hello in mine if hello["device"] in self._names] or mine
        if len(mine) > 1:
            names = ", ".join(hello["device"] for hello in mine)
            raise DeviceError(f"Several {self.label}s are connected ({names}); set ARCHANGEL_DEVICE to choose one")
        self._found = mine[0]["device"] if mine else None
        if mine and mine[0] != self._remembered and self._memory:
            self._memory.save_device(mine[0])
            self._remembered = mine[0]
        return mine[0] if mine else None

    async def _reachable_hello(self) -> dict[str, Any] | None:
        try:
            return await self._hello()
        except httpx.HTTPError as error:
            raise DeviceError(f"Relay unreachable: {error}") from error

    def _last(self) -> dict[str, Any] | None:
        return self._memory.last_device(self.platform) if self._memory else None

    def _mac(self, hello: dict[str, Any] | None) -> str | None:
        """The card to wake it by, if it has one and something can wake it."""
        return (hello or {}).get("wake", {}).get("mac") if self._wake else None

    async def _command(self, type: str, **params: Any) -> dict[str, Any]:
        with activity.call(self.label, "device", " ".join([type, *map(str, params.values())])):
            response = await self._send(type, params)
            if response.status_code == 503:
                # Never reached the device, so resending can't repeat anything. Phones drop off briefly (network
                # switches, the service restarting) and PCs sleep; wait for it rather than failing the task.
                self._found = self._size = None  # it may come back as another device
                await self._wait_online()
                response = await self._send(type, params)
            elif response.status_code == 502 and type in READ_ONLY:
                # Lost mid-command: only safe to resend what doesn't change anything.
                response = await self._send(type, params)
            if response.status_code != 200:
                raise DeviceError(response.json().get("message", f"Relay returned {response.status_code}"))
            reply = response.json()
            if not reply["ok"]:
                raise DeviceError(reply["error"]["message"])
            return reply.get("data") or {}

    async def _send(self, type: str, params: dict[str, Any]) -> httpx.Response:
        try:
            device = await self._name()
            return await self._client.post(f"/devices/{device}/commands", json={"type": type, **params})
        except httpx.HTTPError as error:
            raise DeviceError(f"Relay unreachable: {error}") from error

    async def _wait_online(self) -> None:
        mac = self._mac(self._last())
        seconds = WAKE_WAIT_SECONDS if mac else OFFLINE_WAIT_SECONDS
        deadline = time.monotonic() + seconds
        woken = 0.0
        while time.monotonic() < deadline:
            if mac and time.monotonic() - woken >= WAKE_EVERY_SECONDS:
                woken = time.monotonic()
                await self._send_wake(mac)
            await asyncio.sleep(POLL_SECONDS)
            try:
                if await self._hello():
                    return
            except httpx.HTTPError:
                pass  # relay down too; keep waiting
        tried = " (woken by the phone, it didn't come back: is Wake-on-LAN set up?)" if mac else ""
        raise DeviceError(f"The {self.label} has been offline for {seconds} s{tried}")

    async def _send_wake(self, mac: str) -> None:
        log.info("Waking the %s (%s)", self.label, mac)
        try:
            await self._wake(mac)
        except DeviceError as error:  # the phone's offline too: keep waiting, it may come back by itself
            log.warning("Couldn't wake the %s: %s", self.label, error)
