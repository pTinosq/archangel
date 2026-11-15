"""The bridge running: the settings in, the connection to the relay up, commands to the PC."""

import json
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bridge import protocol
from bridge.config import Settings
from bridge.connection import Connection, Status
from bridge.device import Device
from bridge.executor import Executor

log = logging.getLogger(__name__)


async def serve(settings: Settings, device: Device, status_file: Path | None = None) -> None:
    """Until cancelled: connected to the relay, or trying to be."""

    def hello() -> protocol.Message:
        screen = device.screen()
        return protocol.hello(settings.device, screen.width, screen.height, screen.density, device.mac())

    def status(status: Status) -> None:
        if status_file:
            write_status(status_file, status)

    relay = settings.relay
    log.info("Bridge %s as %r, relay %s", protocol.VERSION, settings.device, relay.url or "(none set)")
    await Connection(relay.url, relay.token, hello, Executor(device).execute, status).run()


def write_status(path: Path, status: Status) -> None:
    """For the settings window: how the connection is doing."""
    try:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({**status.__dict__, "time": time.time()}), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        log.debug("Couldn't write the status", exc_info=True)


def read_status(path: Path) -> Status | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Status(raw.get("state", ""), raw.get("reason", ""), raw.get("delay", 0.0))


def start_logging(path: Path | None) -> None:
    handlers: list[logging.Handler] = []
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
        except OSError:
            pass  # not an administrator yet (the settings window, before it asks): nothing worth keeping is lost
    if sys.stderr:  # the windowed exe has none
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True
    )
