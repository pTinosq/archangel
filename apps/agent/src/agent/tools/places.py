"""Finding a named place (Open-Meteo's geocoding: free, no key), for the tools that answer about somewhere."""

import re
from dataclasses import dataclass

from agent.tools.tool import Tool, ToolError

GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    timezone: str = "auto"
    """An IANA name; "auto" where the source works it out from the coordinates."""


async def find(tool: Tool, place: str) -> Place:
    """`place` as a name ("Shoreditch", "Osaka, Japan") or "lat,lon" (an address they shared)."""
    if match := COORDINATES.match(place):
        lat, lon = float(match[1]), float(match[2])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ToolError(f"{place!r} isn't a place on Earth.")
        return Place(f"{lat:.3f}, {lon:.3f}", lat, lon)
    name = place.split(",")[0].strip()  # the geocoder matches a name, not "Osaka, Japan"
    if not name:
        raise ToolError("Which place? Ask the owner, or use owner_location for where they are.")
    found = (await tool.get(GEOCODING, name=name, count=1, language="en")).get("results") or []
    if not found:
        raise ToolError(f"No place found called {place!r}: try a nearby town, or coordinates.")
    top = found[0]
    where = ", ".join(dict.fromkeys(part for part in (top["name"], top.get("admin1"), top.get("country")) if part))
    return Place(where, top["latitude"], top["longitude"], top.get("timezone", "auto"))
