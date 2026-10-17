import asyncio
import base64
import time
from typing import Any

import httpx

from agent import activity
from agent.messages import Image

SCREENSHOT_SCALE = 0.5
SCREENSHOT_QUALITY = 30  # half the bytes of 80; Jev and Luna locate just as well (tested on Settings)
OFFLINE_WAIT_SECONDS = 60
POLL_SECONDS = 2
READ_ONLY = {"screenshot", "apps", "ui_tree"}


class PhoneError(Exception):
    """The phone couldn't do what was asked. The message is safe to show the model."""


class Phone:
    """The phone, reached through the relay. Positions are fractions of the screen, from the top-left.

    Without a `device` name, it's whichever phone is connected, as long as there's only one.
    """

    def __init__(self, client: httpx.AsyncClient, device: str | None = None):
        self._client = client
        self._device = device
        self._found: str | None = None  # the connected phone, when no name was given
        self._size: tuple[int, int] | None = None  # in pixels, as of the latest screenshot

    async def screenshot(self) -> Image:
        data = await self._command("screenshot", scale=SCREENSHOT_SCALE, quality=SCREENSHOT_QUALITY)
        # Taps are placed on this screen, so they use its size: it changes when the phone rotates.
        self._size = round(data["width"] / SCREENSHOT_SCALE), round(data["height"] / SCREENSHOT_SCALE)
        return Image(base64.b64decode(data["jpeg"]), "image/jpeg")

    async def tap(self, x: float, y: float, hold: bool = False) -> None:
        px, py = await self._pixels(x, y)
        await self._command("tap", x=px, y=py, durationMs=800 if hold else 50)

    async def swipe(self, x1: float, y1: float, x2: float, y2: float) -> None:
        start, end = await self._pixels(x1, y1), await self._pixels(x2, y2)
        await self._command("swipe", x1=start[0], y1=start[1], x2=end[0], y2=end[1])

    async def type(self, text: str) -> None:
        await self._command("type", text=text)

    async def key(self, key: str) -> None:
        await self._command("key", key=key)

    async def launch(self, package: str) -> None:
        await self._command("launch", package=package)

    async def apps(self) -> dict[str, str]:
        """Launchable apps, label → package."""
        data = await self._command("apps")
        return {app["label"]: app["package"] for app in data["apps"]}

    async def _pixels(self, x: float, y: float) -> tuple[int, int]:
        if not self._size:  # no screenshot yet: the size it said hello with
            if not (hello := await self._reachable_hello()):
                raise PhoneError("Phone is not connected")
            self._size = hello["screen"]["width"], hello["screen"]["height"]
        width, height = self._size
        return round(x * width), round(y * height)

    async def _name(self) -> str:
        if not (self._device or self._found) and not await self._reachable_hello():
            await self._wait_online()
        return self._device or self._found

    async def _hello(self) -> dict[str, Any] | None:
        """The phone's hello (name, screen), if it's connected."""
        phones = (await self._client.get("/devices")).json()
        if self._device:
            return next((p for p in phones if p["device"] == self._device), None)
        if len(phones) > 1:
            names = ", ".join(p["device"] for p in phones)
            raise PhoneError(f"Several phones are connected ({names}); set ARCHANGEL_DEVICE to choose one")
        self._found = phones[0]["device"] if phones else None
        return phones[0] if phones else None

    async def _reachable_hello(self) -> dict[str, Any] | None:
        try:
            return await self._hello()
        except httpx.HTTPError as error:
            raise PhoneError(f"Relay unreachable: {error}") from error

    async def _command(self, type: str, **params: Any) -> dict[str, Any]:
        with activity.call("phone", "phone", " ".join([type, *map(str, params.values())])):
            response = await self._send(type, params)
            if response.status_code == 503:
                # Never reached the phone, so resending can't repeat anything. Phones drop off briefly (network
                # switches, the service restarting); wait for it rather than failing the task.
                self._found = self._size = None  # it may come back as another phone
                await self._wait_online()
                response = await self._send(type, params)
            elif response.status_code == 502 and type in READ_ONLY:
                # Lost mid-command: only safe to resend what doesn't change anything.
                response = await self._send(type, params)
            if response.status_code != 200:
                raise PhoneError(response.json().get("message", f"Relay returned {response.status_code}"))
            reply = response.json()
            if not reply["ok"]:
                raise PhoneError(reply["error"]["message"])
            return reply.get("data") or {}

    async def _send(self, type: str, params: dict[str, Any]) -> httpx.Response:
        try:
            device = await self._name()
            return await self._client.post(f"/devices/{device}/commands", json={"type": type, **params})
        except httpx.HTTPError as error:
            raise PhoneError(f"Relay unreachable: {error}") from error

    async def _wait_online(self) -> None:
        deadline = time.monotonic() + OFFLINE_WAIT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_SECONDS)
            try:
                if await self._hello():
                    return
            except httpx.HTTPError:
                pass  # relay down too; keep waiting
        raise PhoneError(f"Phone has been offline for {OFFLINE_WAIT_SECONDS} s")
