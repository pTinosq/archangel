import asyncio
import time
from urllib.parse import urlsplit

import httpx

from agent.events import Event, Notice
from agent.links import Links
from agent.location import Whereabouts
from agent.messages import Address
from agent.store import Location
from agent.web import Web

SITE = "https://archangel.example"
CALL = Address("voice", "CA1")


class Owner:
    """Shares where they are through Archangel's link, with OpenStreetMap faked."""

    def __init__(self, store, address: str | None = "1 Liverpool Street, London"):
        self.notices: list[Event] = []

        def nominatim(request: httpx.Request) -> httpx.Response:
            if address is None:
                return httpx.Response(503)
            return httpx.Response(200, json={"display_name": address})

        web = Web()
        self.whereabouts = Whereabouts(
            store, web, Links(store, web, "secret", SITE), httpx.AsyncClient(transport=httpx.MockTransport(nominatim))
        )

        async def notify(event: Event) -> None:
            self.notices.append(event)

        self.whereabouts.notify = notify
        self.app = web.finish()

    def opens(self, link: str) -> httpx.Response:
        return self._request("GET", link)

    def shares(self, link: str) -> httpx.Response:
        return self._request("POST", link, json={"lat": 51.5178, "lon": -0.0823, "accuracy": 12.4})

    def _request(self, method: str, link: str, **kwargs) -> httpx.Response:
        async def go():
            transport = httpx.ASGITransport(self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                response = await client.request(method, _local(link), **kwargs)
            await asyncio.sleep(0)  # the orchestrator is told in the background
            return response

        return asyncio.run(go())


def _local(link: str) -> str:
    assert link.startswith(SITE)
    url = urlsplit(link)
    return f"{url.path}?{url.query}"


def test_the_owner_shares_where_they_are_from_a_link(store):
    owner = Owner(store)
    link = owner.whereabouts.link(CALL)
    assert "geolocation" in owner.opens(link).text

    assert owner.shares(link).json() == {"ok": True}
    shared = owner.whereabouts.latest()
    assert (shared.lat, shared.lon, shared.address) == (51.5178, -0.0823, "1 Liverpool Street, London")
    assert time.time() - shared.time < 5
    # The orchestrator hears, where it asked from, to carry on with what needed it.
    (notice,) = owner.notices
    assert notice == Notice(f"The owner shared where they are: {shared}", CALL)
    assert "1 Liverpool Street, London (51.51780, -0.08230, within 12 m)" in notice.text


def test_a_forged_or_altered_link_shares_nothing(store):
    owner = Owner(store)
    link = owner.whereabouts.link(CALL)
    assert owner.shares(link.replace("CA1", "CA2")).status_code == 404
    assert owner.shares(link.replace("sig=", "sig=0")).status_code == 404
    assert owner.opens(link.replace("sig=", "sig=0")).status_code == 404
    assert owner.whereabouts.latest() is None and owner.notices == []


def test_without_an_address_the_coordinates_still_do(store):
    owner = Owner(store, address=None)
    owner.shares(owner.whereabouts.link(Address("sms", "+447700900123")))
    assert str(owner.whereabouts.latest()) == "51.51780, -0.08230, within 12 m"


def test_without_a_public_address_theres_no_link(store):
    assert Whereabouts(store, Web(), None).link(CALL) is None


def test_where_they_were_is_kept(store):
    store.save_location(Location(51.5, -0.1, 20, "Somewhere", 1000.0))
    store.save_location(Location(52.2, 0.12, 5, "Cambridge", 2000.0))
    assert store.location() == Location(52.2, 0.12, 5, "Cambridge", 2000.0)
