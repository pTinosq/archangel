"""The weather now and for the days ahead, anywhere (Open-Meteo: free, no key), with sunrise and sunset."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from agent.tools import places
from agent.tools.tool import Setting, Tool

FORECAST = "https://api.open-meteo.com/v1/forecast"
MAX_DAYS = 7
HOURS = 24
HOURLY_FIRST = 12  # hours shown one by one; after those, every third

# WMO weather codes, as Open-Meteo reports them.
CONDITIONS = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


class Weather(Tool):
    name = "weather"
    title = "Weather"
    about = "weather now, by the hour today and for the next days, sunrise and sunset, anywhere"
    settings = (Setting("place", "Default place, when none is asked about (e.g. London)"),)

    def functions(self) -> list[Callable[..., Awaitable[str]]]:
        return [self.weather]

    async def weather(self, place: str = "", days: int = 3) -> str:
        """The weather at a place, in seconds: now, hour by hour for the next day (for "will it rain at 5?"), then
        each day (`days`, up to 7) with sunrise and sunset. `place`: a name ("Shoreditch", "Osaka, Japan") or
        "lat,lon"; for where the owner is, use owner_location first. Not for what the weather was in the past."""
        found = await places.find(self, place.strip() or self.setting("place"))
        data = await self.get(
            FORECAST,
            latitude=found.lat,
            longitude=found.lon,
            current="temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            hourly="temperature_2m,precipitation_probability,weather_code",
            daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
            forecast_hours=HOURS,
            forecast_days=max(1, min(days, MAX_DAYS)),
            wind_speed_unit="mph",
            timezone=found.timezone,
        )
        now, hourly, daily = data["current"], data["hourly"], data["daily"]
        lines = [
            f"{found.name}, {_time(now['time'])} local: {_condition(now['weather_code'])}, {now['temperature_2m']:.0f}°C "
            f"(feels {now['apparent_temperature']:.0f}°), wind {now['wind_speed_10m']:.0f} mph, "
            f"{now['precipitation']} mm of rain in the last hour."
        ]
        keys = ("time", "temperature_2m", "precipitation_probability", "weather_code")
        rows = list(zip(*(hourly[key] for key in keys), strict=True))
        shown = rows[:HOURLY_FIRST] + rows[HOURLY_FIRST::3]
        hours = "; ".join(f"{_time(t)} {_condition(c)} {d:.0f}°C {_chance(p)} rain" for t, d, p, c in shown)
        lines.append(f"Next hours (local): {hours}")
        for i, day in enumerate(daily["time"]):
            sun = f"{_time(daily['sunrise'][i])}–{_time(daily['sunset'][i])}"
            lines.append(
                f"{datetime.fromisoformat(day):%a %-d %b}: {_condition(daily['weather_code'][i])}, "
                f"{daily['temperature_2m_min'][i]:.0f}–{daily['temperature_2m_max'][i]:.0f}°C, "
                f"{_chance(daily['precipitation_probability_max'][i])} rain, sun {sun}"
            )
        return "\n".join(lines)


def _condition(code: int) -> str:
    return CONDITIONS.get(code, f"weather code {code}")


def _chance(percent: int | None) -> str:
    return "?" if percent is None else f"{percent}%"


def _time(stamp: str) -> str:
    return f"{datetime.fromisoformat(stamp):%H:%M}"
