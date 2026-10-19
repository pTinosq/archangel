"""Where the owner is, for "near me" and pickups: the phone sits at home, the owner doesn't.

They share it when it's needed, from their browser, through a signed link the orchestrator texts them: nothing runs
on their phone in between. The latest is kept, with when, so a recent one is reused. It comes back to the
orchestrator as a notice, to carry on with whatever needed it.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx
from fastapi import Response
from pydantic import BaseModel

from agent.events import Event, Notice
from agent.links import Links
from agent.messages import Address
from agent.store import Location, Store
from agent.web import Web

log = logging.getLogger(__name__)

# OpenStreetMap's free reverse geocoder: one request a share is well within its usage policy.
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"


class Shared(BaseModel):
    lat: float
    lon: float
    accuracy: float


class Whereabouts:
    notify: Callable[[Event], Awaitable[None]]
    """Told when the owner shares where they are (the orchestrator sets it)."""

    def __init__(self, store: Store, web: Web, links: Links | None, client: httpx.AsyncClient | None = None):
        """Without `links` (no public address) there's no page to share from, only what the owner says."""
        self._store = store
        self._links = links
        self._client = client or httpx.AsyncClient(timeout=10, headers={"User-Agent": "Archangel"})
        self._telling: set[asyncio.Task] = set()

        if links:  # no public address: no page to share from

            @web.app.get("/where/{channel}/{id}")
            async def page(channel: str, id: str, expires: int = 0, sig: str = "") -> Response:
                links.check(_path(Address(channel, id)), expires, sig)
                return Response(PAGE, media_type="text/html", headers={"Cache-Control": "no-store"})

            @web.app.post("/where/{channel}/{id}")
            async def share(channel: str, id: str, shared: Shared, expires: int = 0, sig: str = "") -> dict:
                to = Address(channel, id)
                links.check(_path(to), expires, sig)
                await self._shared(shared, to)
                return {"ok": True}

    def link(self, reply_to: Address) -> str | None:
        """A link that shares where the owner is now; the notice that it arrived goes to `reply_to`."""
        return self._links.signed(_path(reply_to)) if self._links else None

    def latest(self) -> Location | None:
        return self._store.location()

    async def _shared(self, shared: Shared, to: Address) -> None:
        location = Location(shared.lat, shared.lon, shared.accuracy, await self._address(shared), time.time())
        self._store.save_location(location)
        log.info("The owner shared where they are")
        # Not waited for: the orchestrator may be busy, and the page only needs to know it arrived.
        telling = asyncio.create_task(self.notify(Notice(f"The owner shared where they are: {location}", to)))
        self._telling.add(telling)
        telling.add_done_callback(self._telling.discard)

    async def _address(self, at: Shared) -> str:
        try:
            response = await self._client.get(NOMINATIM, params={"lat": at.lat, "lon": at.lon, "format": "jsonv2"})
            response.raise_for_status()
            return response.json().get("display_name", "")
        except (httpx.HTTPError, ValueError):  # the coordinates still do
            log.warning("Couldn't find the address", exc_info=True)
            return ""


def _path(reply_to: Address) -> str:
    return f"/where/{quote(reply_to.channel, safe='')}/{quote(reply_to.id, safe='')}"


# Shares at once (a browser that's allowed before doesn't ask again); the button is for a refusal or a retry.
PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Archangel</title>
<style>
  body { background: #000; color: #e9e9e4; font: 16px/1.5 ui-monospace, monospace; margin: 0; padding: 32px 24px; }
  h1 { font-size: 12px; letter-spacing: .2em; text-transform: uppercase; color: #c6ff3d; margin: 0 0 24px; }
  button { font: inherit; background: #c6ff3d; color: #000; border: 0; padding: 12px 20px; margin-top: 16px; }
  button[hidden] { display: none; }
</style>
<h1>Archangel</h1>
<p id="note">Finding where you are…</p>
<button id="again" hidden>Share where I am</button>
<script>
  const note = document.getElementById("note"), again = document.getElementById("again");
  function share() {
    again.hidden = true;
    note.textContent = "Finding where you are…";
    navigator.geolocation.getCurrentPosition(async (position) => {
      const { latitude: lat, longitude: lon, accuracy } = position.coords;
      const response = await fetch(location.href, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lat, lon, accuracy }),
      }).catch(() => null);
      note.textContent = response?.ok ? "Sent. You can close this." : "Couldn't send it. Try again?";
      again.hidden = !!response?.ok;
    }, (error) => {
      note.textContent = error.code === error.PERMISSION_DENIED
        ? "Location is blocked for this site: allow it in the browser's settings, then try again."
        : "Couldn't find where you are. Try again?";
      again.hidden = false;
    }, { enableHighAccuracy: true, timeout: 30000 });
  }
  again.onclick = share;
  share();
</script>
</html>
"""
