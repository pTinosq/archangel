"""The bridge's settings: two relays (the laptop's while developing, the deployed one the rest of the time), which is
on, the PC's name, and the PIN or password that unlocks it. One JSON file, so one save is one change.
"""

import base64
import json
import os
import re
import socket
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

Mode = Literal["dev", "prod"]


@dataclass(frozen=True)
class Relay:
    url: str = ""
    token: str = ""


@dataclass(frozen=True)
class Settings:
    mode: Mode = "prod"
    dev: Relay = field(default_factory=Relay)
    prod: Relay = field(default_factory=Relay)
    device: str = ""
    secret: str = ""
    """The PIN or password the lock screen takes; "" for none."""

    @property
    def relay(self) -> Relay:
        return self.dev if self.mode == "dev" else self.prod

    def with_relay(self, relay: Relay) -> "Settings":
        """These settings with the current mode's relay replaced."""
        return replace(self, dev=relay) if self.mode == "dev" else replace(self, prod=relay)


def default_device() -> str:
    """The computer's name, as a relay device name: "DESKTOP-4F2K" → "desktop-4f2k"."""
    return re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-") or "pc"


def folder() -> Path:
    """Where the service keeps its settings, status and logs: readable only by administrators and SYSTEM."""
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Archangel"


class Store:
    """Settings on disk. `protect` and `unprotect` keep the secret encrypted at rest (Windows DPAPI in use)."""

    def __init__(
        self,
        path: Path,
        protect: Callable[[bytes], bytes] = lambda data: data,
        unprotect: Callable[[bytes], bytes] = lambda data: data,
    ):
        self.path = path
        self._protect = protect
        self._unprotect = unprotect

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Settings(device=default_device())
        secret = raw.get("secret", "")
        return Settings(
            mode="dev" if raw.get("mode") == "dev" else "prod",
            dev=Relay(**raw.get("dev", {})),
            prod=Relay(**raw.get("prod", {})),
            device=raw.get("device") or default_device(),
            secret=self._unprotect(base64.b64decode(secret)).decode() if secret else "",
        )

    def save(self, settings: Settings) -> None:
        raw = asdict(settings)
        raw["secret"] = base64.b64encode(self._protect(settings.secret.encode())).decode() if settings.secret else ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        temporary.replace(self.path)  # whole or not at all: the service may be reading it
