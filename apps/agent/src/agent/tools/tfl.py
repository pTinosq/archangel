"""London transport, live (TfL's open API): whether a line is running and why not, journeys, and what's arriving at a
stop. Works without a key; an app key only raises the rate limit."""

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from urllib.parse import quote

import httpx

from agent.schedule import TIMEZONE
from agent.tools.tool import Setting, Tool, ToolError

API = "https://api.tfl.gov.uk"
MODES = "tube,overground,dlr,elizabeth-line,tram"
GOOD = "Good Service"
JOURNEYS_SHOWN = 3
ARRIVALS_SHOWN = 6
STOPS_SHOWN = 2
STOP_ID = re.compile(r"^(490|940|910|HUB)[0-9A-Za-z]+$")  # an id the owner may know, e.g. a bus stop's


class TfL(Tool):
    name = "tfl"
    title = "TfL"
    about = "London tube, Overground, DLR, Elizabeth line and tram status and why; journeys; next buses and trains"
    settings = (Setting("app_key", "App key (optional: only raises TfL's rate limit)", secret=True),)

    def functions(self) -> list[Callable[..., Awaitable[str]]]:
        return [self.tfl_status, self.tfl_journey, self.tfl_arrivals]

    async def tfl_status(self, lines: str = "") -> str:
        """Whether London's lines are running, and why not, right now. `lines`: e.g. "Victoria, Northern"; leave it
        empty for every tube, Overground, DLR, Elizabeth line and tram line. Not for buses or National Rail."""
        wanted = [_line_id(line) for line in lines.split(",") if line.strip()]
        try:
            found = await self._get(f"/Line/{','.join(wanted)}/Status" if wanted else f"/Line/Mode/{MODES}/Status")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                raise ToolError(f"TfL has no line called {lines!r}: try e.g. Victoria, Elizabeth, Windrush.") from error
            raise
        running = [line["name"] for line in found if _statuses(line) == {GOOD}]
        report = [f"{line['name']}: {_problem(line)}" for line in found if _statuses(line) != {GOOD}]
        if running:
            report.insert(0, f"Good service: {', '.join(running)}.")
        return "\n".join(report) or "TfL lists nothing for that."

    async def tfl_journey(self, origin: str, destination: str, when: str = "", arriving: bool = False) -> str:
        """A journey across London and the UK by tube, bus, rail, walking, live with today's disruption: "how do I
        get to Shoreditch?" (origin is where they are: ask, or use owner_location). Places as names, postcodes or
        "lat,lon". `when`: "HH:MM" today or "YYYY-MM-DD HH:MM", default now; `arriving`: that's when to arrive by."""
        at = _moment(when)
        journeys, assumed = await self._journeys(origin, destination, at, arriving)
        if not journeys:
            return "TfL found no journey for that."
        text = [
            f"{_hhmm(j['startDateTime'])}–{_hhmm(j['arrivalDateTime'])} ({j['duration']} min): {_legs(j)}"
            for j in journeys[:JOURNEYS_SHOWN]
        ]
        return "\n".join([*assumed, *text])

    async def tfl_arrivals(self, stop: str, line: str = "", mode: str = "") -> str:
        """What's due at a stop or station, in minutes: "when's the 59?", "next Northern line from Angel". `stop`: its
        name (or TfL's id); `line`: only that line or bus number; `mode`: bus, tube, overground, dlr…, to tell
        a bus stop from a station of the same name."""
        stops = (
            [(stop, stop)]
            if STOP_ID.match(stop.strip())
            else [
                (m["id"], f"{m['name']}{f' (stop {m['stopLetter']})' if m.get('stopLetter') else ''}")
                for m in (
                    await self._get("/StopPoint/Search", query=stop, modes=mode.strip().lower() or None, maxResults=5)
                )["matches"]
                if not m["id"].startswith("HUB")  # a hub has no arrivals of its own
            ]
        )
        if not stops:
            raise ToolError(f"TfL knows no stop called {stop!r}.")
        answers = []
        for stop_id, name in stops[:STOPS_SHOWN]:
            due = sorted(await self._get(f"/StopPoint/{stop_id}/Arrivals"), key=lambda a: a["timeToStation"])
            due = [a for a in due if not line or a["lineName"].lower() == line.strip().lower()]
            if due:
                answers.append(f"{name}: " + "; ".join(_arrival(a) for a in due[:ARRIVALS_SHOWN]))
        return "\n".join(answers) or f"Nothing due at {stop}" + (f" on {line}" if line else "") + "."

    async def _journeys(self, origin: str, destination: str, at: datetime | None, arriving: bool):
        """TfL answers a name it can't place with a list of what it might mean: the best match is used, and said."""
        assumed, places = [], {"from": origin.strip(), "to": destination.strip()}
        for _ in range(2):  # the first try; and again with the matches chosen
            data = await self._get(
                f"/Journey/JourneyResults/{quote(places['from'], safe='')}/to/{quote(places['to'], safe='')}",
                accept=(300,),
                date=at and f"{at:%Y%m%d}",
                time=at and f"{at:%H%M}",
                timeIs="Arriving" if arriving else "Departing",
            )
            if "journeys" in data:
                return data["journeys"], assumed
            for side in ("from", "to"):
                match = data.get(f"{side}LocationDisambiguation") or {}
                if options := match.get("disambiguationOptions"):
                    best = max(options, key=lambda o: o.get("matchQuality", 0))
                    if match.get("matchStatus") == "list":
                        assumed.append(f"Took {places[side]!r} to mean {best['place']['commonName']}.")
                        places[side] = best["parameterValue"]
        raise ToolError(f"TfL couldn't place {origin!r} or {destination!r}: try a postcode or station name.")

    async def _get(self, path: str, **params):
        try:
            return await self.get(API + path, app_key=self.setting("app_key") or None, **params)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                raise ToolError("TfL's rate limit is hit: try again in a minute.") from error
            raise


def _line_id(name: str) -> str:
    """"Hammersmith & City line" → "hammersmith-city": TfL's ids."""
    name = re.sub(r"\bline\b", "", name.lower().replace("&", " ")).strip()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def _statuses(line: dict) -> set[str]:
    return {s["statusSeverityDescription"] for s in line["lineStatuses"]}


def _problem(line: dict) -> str:
    return "; ".join(sorted({s.get("reason") or s["statusSeverityDescription"] for s in line["lineStatuses"]}))


def _moment(when: str) -> datetime | None:
    if not when.strip():
        return None
    try:
        if len(when.strip()) <= 5:  # a time today
            return datetime.combine(datetime.now(TIMEZONE).date(), datetime.strptime(when.strip(), "%H:%M").time())
        return datetime.fromisoformat(when.strip())
    except ValueError as error:
        raise ToolError(f"Give `when` as HH:MM or YYYY-MM-DD HH:MM, not {when!r}.") from error


def _hhmm(stamp: str) -> str:
    return f"{datetime.fromisoformat(stamp):%H:%M}"


def _legs(journey: dict) -> str:
    return "; ".join(f"{leg['instruction']['summary']} ({leg['duration']} min)" for leg in journey["legs"])


def _arrival(arrival: dict) -> str:
    minutes = round(arrival["timeToStation"] / 60)
    due = "due" if minutes < 1 else f"{minutes} min"
    platform = arrival.get("platformName")
    return f"{arrival['lineName']} to {arrival['destinationName']} {due}" + (
        f" ({platform})" if platform and platform != "null" else ""
    )
