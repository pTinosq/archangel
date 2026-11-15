import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import websockets
from websockets.asyncio.client import connect

from bridge.protocol import Message

log = logging.getLogger(__name__)

PING_SECONDS = 15
MIN_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


@dataclass(frozen=True)
class Status:
    state: str
    """off, unconfigured, connecting, connected or retrying."""
    reason: str = ""
    delay: float = 0.0


class Connection:
    """Keeps one WebSocket to the relay open until cancelled, reconnecting with backoff.
    Each incoming frame is passed to `handle` and its reply sent back."""

    def __init__(
        self,
        url: str,
        token: str,
        hello: Callable[[], Message],
        handle: Callable[[str], Awaitable[Message]],
        status: Callable[[Status], None] = lambda _: None,
    ):
        """`hello` is called at each connection: the screen may have changed since the last."""
        self._url = url
        self._token = token
        self._hello = hello
        self._handle = handle
        self._status = status

    async def run(self) -> None:
        try:
            if not self._url:
                self._update(Status("unconfigured"))
                await asyncio.Event().wait()
            failures = 0
            while True:
                self._update(Status("connecting"))
                opened, reason = await self._session()
                failures = 1 if opened else failures + 1
                delay = backoff(failures)
                self._update(Status("retrying", reason, delay))
                await asyncio.sleep(delay)
        finally:
            self._update(Status("off"))

    async def _session(self) -> tuple[bool, str]:
        """One connection, until it ends: whether it opened, and why it ended."""
        try:
            socket = await connect(
                self._url,
                additional_headers={"Authorization": f"Bearer {self._token}"},
                ping_interval=PING_SECONDS,
                ping_timeout=PING_SECONDS,
                open_timeout=PING_SECONDS,
                max_size=None,
            )
        except websockets.InvalidStatus as error:
            return False, f"HTTP {error.response.status_code}"
        except (OSError, TimeoutError, websockets.WebSocketException, ValueError) as error:
            return False, str(error) or type(error).__name__
        commands: set[asyncio.Task] = set()
        try:
            await socket.send(json.dumps(self._hello()))
            self._update(Status("connected"))
            async for frame in socket:
                if isinstance(frame, str):
                    command = asyncio.create_task(self._reply(socket, frame))
                    commands.add(command)
                    command.add_done_callback(commands.discard)
            return True, f"Closed by relay ({socket.close_code})"
        except websockets.ConnectionClosed as error:
            return True, f"Connection lost ({error.rcvd.code if error.rcvd else 'no close frame'})"
        except OSError as error:
            return True, str(error) or type(error).__name__
        finally:
            # Commands still queued belong to a dead connection; the relay has already given up on them.
            for command in commands:
                command.cancel()
            await socket.close()

    async def _reply(self, socket, frame: str) -> None:
        reply = await self._handle(frame)
        try:
            await socket.send(json.dumps(reply))
        except websockets.ConnectionClosed:
            pass  # the relay has failed the command already

    def _update(self, status: Status) -> None:
        log.info("%s%s", status.state, f": {status.reason}" if status.reason else "")
        self._status(status)


def backoff(failures: int, rng: random.Random = random._inst) -> float:
    """Exponential backoff with jitter: 1 s, 2 s, 4 s… capped at 30 s, each scaled by 50–100%."""
    ceiling = min(MAX_BACKOFF_SECONDS, MIN_BACKOFF_SECONDS * 2 ** min(max(failures - 1, 0), 5))
    return ceiling * rng.uniform(0.5, 1.0)
