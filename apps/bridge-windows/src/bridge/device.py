from dataclasses import dataclass
from typing import Protocol

from bridge.protocol import Message


@dataclass(frozen=True)
class Screen:
    width: int
    height: int
    density: float


@dataclass(frozen=True)
class Shot:
    width: int
    height: int
    jpeg: bytes


class Device(Protocol):
    """What the bridge can do to the PC. Coordinates are physical pixels of the primary display."""

    def screen(self) -> Screen: ...

    def mac(self) -> str | None:
        """The network card to wake the PC by, if there is one."""
        ...

    async def screenshot(self, scale: float, quality: int) -> Shot: ...
    async def tap(self, x: int, y: int, duration_ms: int, button: str, count: int) -> None: ...
    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None: ...
    async def scroll(self, x: int, y: int, dx: int, dy: int) -> None: ...
    async def type(self, text: str) -> None: ...
    async def key(self, key: str) -> None: ...
    async def launch(self, app: str) -> None: ...
    async def apps(self) -> list[Message]: ...
    async def ui_tree(self) -> list[Message]: ...
    async def unlock(self) -> None: ...


class CommandError(Exception):
    """An expected failure, reported to the caller with `code` (see `ErrorCode`)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
