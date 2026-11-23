import asyncio
import json
from datetime import datetime

import httpx
import pytest
from pydantic_ai import Agent, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.schedule import TIMEZONE
from agent.tools import BUILT_IN, Setting, Tool, ToolError, Tools
from agent.tools.dates import Dates
from agent.tools.inbox import Inbox, addresses
from agent.tools.currency import Currency
from agent.tools.tfl import TfL
from agent.tools.weather import Weather


class Sources:
    """The APIs the tools call: JSON by path (or a status), and every request made, for what was asked."""

    def __init__(self, **answers):
        self.answers = {path.replace("__", "/"): answer for path, answer in answers.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.get(request.url.path.strip("/"), 404)
        if isinstance(answer, int):
            return httpx.Response(answer, json={"message": "no"})
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        return httpx.Response(status, json=body)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def tools(store, sources: Sources, kinds=BUILT_IN) -> Tools:
    return Tools(store, sources.client(), kinds)


def tool(store, sources: Sources, kind: type[Tool]):
    return tools(store, sources, [kind])._tools[kind.name]


def line(name, *statuses):
    return {"id": name.lower(), "name": name, "lineStatuses": [{"statusSeverityDescription": s[0], "reason": s[1]} for s in statuses]}


GOOD = ("Good Service", None)


def test_tfl_status_says_which_lines_are_running_and_why_not(store):
    sources = Sources(**{
        "Line__Mode__tube,overground,dlr,elizabeth-line,tram__Status": [
            line("Victoria", GOOD),
            line("Northern", ("Minor Delays", "Minor delays between Camden Town and Kennington due to a signal failure.")),
            line("DLR", GOOD),
        ]
    })
    answer = asyncio.run(tool(store, sources, TfL).tfl_status())
    assert answer == (
        "Good service: Victoria, DLR.\n"
        "Northern: Minor delays between Camden Town and Kennington due to a signal failure."
    )


def test_tfl_status_for_named_lines_uses_tfls_ids(store):
    sources = Sources(**{"Line__hammersmith-city,elizabeth__Status": [line("Hammersmith & City", GOOD), line("Elizabeth line", GOOD)]})
    answer = asyncio.run(tool(store, sources, TfL).tfl_status("Hammersmith & City line, Elizabeth"))
    assert answer == "Good service: Hammersmith & City, Elizabeth line."


def test_tfl_status_for_a_line_that_does_not_exist_is_an_answer(store):
    with pytest.raises(ToolError, match="no line called 'Metropolitan Express'"):
        asyncio.run(tool(store, Sources(), TfL).tfl_status("Metropolitan Express"))


def test_tfl_app_key_is_sent_when_set(store):
    sources = Sources(**{"Line__victoria__Status": [line("Victoria", GOOD)]})
    all_tools = tools(store, sources)
    asyncio.run(all_tools._tools["tfl"].tfl_status("victoria"))
    assert "app_key" not in sources.requests[-1].url.params
    all_tools.configure("tfl", settings={"app_key": "k3y"})
    asyncio.run(all_tools._tools["tfl"].tfl_status("victoria"))
    assert sources.requests[-1].url.params["app_key"] == "k3y"


def journey(start, arrive, minutes, *legs):
    return {
        "startDateTime": start,
        "arrivalDateTime": arrive,
        "duration": minutes,
        "legs": [{"duration": m, "instruction": {"summary": s}} for s, m in legs],
    }


def test_tfl_journey_lists_the_quickest_options(store):
    options = [
        journey("2026-11-17T14:05:00", "2026-11-17T14:40:00", 35, ("Walk to Angel Station", 4), ("Northern line to Bank", 9)),
        journey("2026-11-17T14:10:00", "2026-11-17T14:50:00", 40, ("Bus 205 to Liverpool Street", 38)),
    ]
    sources = Sources(**{"Journey__JourneyResults__Angel__to__Shoreditch High Street": {"journeys": options}})
    answer = asyncio.run(
        tool(store, sources, TfL).tfl_journey("Angel", "Shoreditch High Street", when="2026-11-17 14:00", arriving=False)
    )
    assert answer.splitlines() == [
        "14:05–14:40 (35 min): Walk to Angel Station (4 min); Northern line to Bank (9 min)",
        "14:10–14:50 (40 min): Bus 205 to Liverpool Street (38 min)",
    ]
    asked = sources.requests[0].url.params
    assert (asked["date"], asked["time"], asked["timeIs"]) == ("20261117", "1400", "Departing")


def test_tfl_journey_takes_the_best_match_for_an_unclear_place_and_says_so(store):
    unclear = {
        "toLocationDisambiguation": {
            "matchStatus": "list",
            "disambiguationOptions": [
                {"parameterValue": "1000001", "matchQuality": 400, "place": {"commonName": "Elm Park Rd"}},
                {"parameterValue": "1000002", "matchQuality": 900, "place": {"commonName": "Elm Park Station"}},
            ],
        },
        "fromLocationDisambiguation": {"matchStatus": "identified", "disambiguationOptions": []},
    }
    found = {"journeys": [journey("2026-11-17T09:00:00", "2026-11-17T09:20:00", 20, ("District line to Elm Park", 20))]}
    sources = Sources(**{
        "Journey__JourneyResults__Angel__to__Elm Park": (300, unclear),
        "Journey__JourneyResults__Angel__to__1000002": found,
    })
    answer = asyncio.run(tool(store, sources, TfL).tfl_journey("Angel", "Elm Park"))
    assert answer.splitlines() == [
        "Took 'Elm Park' to mean Elm Park Station.",
        "09:00–09:20 (20 min): District line to Elm Park (20 min)",
    ]


def test_tfl_journey_rejects_a_time_it_cannot_read(store):
    with pytest.raises(ToolError, match="HH:MM"):
        asyncio.run(tool(store, Sources(), TfL).tfl_journey("Angel", "Bank", when="soon"))


def test_tfl_arrivals_for_a_named_stop(store):
    sources = Sources(**{
        "StopPoint__Search": {"matches": [{"id": "490000077E", "name": "Angel", "stopLetter": "E"}, {"id": "HUBANG", "name": "Angel"}]},
        "StopPoint__490000077E__Arrivals": [
            {"lineName": "73", "destinationName": "Victoria", "timeToStation": 420},
            {"lineName": "205", "destinationName": "Paddington", "timeToStation": 30},
            {"lineName": "73", "destinationName": "Stoke Newington", "timeToStation": 100, "platformName": "null"},
        ],
    })
    two = tool(store, sources, TfL)
    assert asyncio.run(two.tfl_arrivals("Angel", mode="bus")) == (
        "Angel (stop E): 205 to Paddington due; 73 to Stoke Newington 2 min; 73 to Victoria 7 min"
    )
    assert sources.requests[0].url.params["modes"] == "bus"
    assert asyncio.run(two.tfl_arrivals("Angel", line="73")) == "Angel (stop E): 73 to Stoke Newington 2 min; 73 to Victoria 7 min"
    assert asyncio.run(two.tfl_arrivals("Angel", line="38")) == "Nothing due at Angel on 38."


def test_tfl_arrivals_for_a_stop_id_skips_the_search(store):
    sources = Sources(**{"StopPoint__490000077E__Arrivals": [{"lineName": "73", "destinationName": "Victoria", "timeToStation": 60}]})
    asyncio.run(tool(store, sources, TfL).tfl_arrivals("490000077E"))
    assert [r.url.path for r in sources.requests] == ["/StopPoint/490000077E/Arrivals"]


def forecast(**changes):
    hours = [f"2026-11-17T{h:02d}:00" for h in range(14, 24)] + [f"2026-11-18T{h:02d}:00" for h in range(0, 14)]
    data = {
        "current": {
            "time": "2026-11-17T14:15", "temperature_2m": 9.4, "apparent_temperature": 6.8,
            "precipitation": 0.0, "weather_code": 3, "wind_speed_10m": 11.2,
        },
        "hourly": {
            "time": hours, "temperature_2m": [9.0] * 24, "weather_code": [61 if h.endswith("17:00") else 3 for h in hours],
            "precipitation_probability": [70 if h.endswith("17:00") else 10 for h in hours],
        },
        "daily": {
            "time": ["2026-11-17", "2026-11-18"], "weather_code": [3, 63],
            "temperature_2m_max": [10.2, 8.0], "temperature_2m_min": [5.1, 4.0],
            "precipitation_probability_max": [70, None],
            "sunrise": ["2026-11-17T07:12", "2026-11-18T07:14"], "sunset": ["2026-11-17T16:06", "2026-11-18T16:05"],
        },
    }
    return data | changes


PLACE = {"results": [{"name": "Shoreditch", "admin1": "England", "country": "United Kingdom", "latitude": 51.52, "longitude": -0.08, "timezone": "Europe/London"}]}


def test_weather_gives_now_the_next_hours_and_the_days(store):
    sources = Sources(**{"v1__search": PLACE, "v1__forecast": forecast()})
    answer = asyncio.run(tool(store, sources, Weather).weather("Shoreditch, London", days=2))
    lines = answer.splitlines()
    assert lines[0] == (
        "Shoreditch, England, United Kingdom, 14:15 local: overcast, 9°C (feels 7°), wind 11 mph, 0.0 mm of rain in the last hour."
    )
    assert "17:00 light rain 9°C 70% rain" in lines[1]
    assert lines[1].count(";") == 12 + 4 - 1  # a dozen hours one by one, then every third
    assert lines[2] == "Tue 17 Nov: overcast, 5–10°C, 70% rain, sun 07:12–16:06"
    assert lines[3] == "Wed 18 Nov: rain, 4–8°C, ? rain, sun 07:14–16:05"
    asked = {r.url.path: r.url.params for r in sources.requests}
    assert asked["/v1/search"]["name"] == "Shoreditch"  # not "Shoreditch, London"
    assert (asked["/v1/forecast"]["latitude"], asked["/v1/forecast"]["timezone"]) == ("51.52", "Europe/London")
    assert asked["/v1/forecast"]["forecast_days"] == "2"


def test_weather_takes_coordinates_and_a_default_place(store):
    sources = Sources(**{"v1__search": PLACE, "v1__forecast": forecast()})
    all_tools = tools(store, sources, [Weather])
    weather = all_tools._tools["weather"]
    asyncio.run(weather.weather("51.5,-0.12", days=99))
    assert sources.requests[-1].url.params["timezone"] == "auto"
    assert sources.requests[-1].url.params["forecast_days"] == "7"
    with pytest.raises(ToolError, match="Which place"):
        asyncio.run(weather.weather())
    all_tools.configure("weather", settings={"place": "Shoreditch"})
    assert asyncio.run(weather.weather()).startswith("Shoreditch, England")


def test_weather_for_an_unknown_place_is_an_answer(store):
    sources = Sources(**{"v1__search": {}})
    with pytest.raises(ToolError, match="No place found called 'Nowhereville'"):
        asyncio.run(tool(store, sources, Weather).weather("Nowhereville"))


def test_currency_converts_at_the_rate_of_a_day(store):
    sources = Sources(**{"v1__2026-01-02": {"amount": 50.0, "base": "EUR", "date": "2026-01-02", "rates": {"GBP": 43.5}}})
    answer = asyncio.run(tool(store, sources, Currency).convert_currency(50, "eur", "gbp", on="2026-01-02"))
    assert answer == "50 EUR = 43.50 GBP (ECB rate of 2026-01-02: 1 EUR = 0.8700 GBP)."
    assert dict(sources.requests[0].url.params) == {"base": "EUR", "symbols": "GBP", "amount": "50"}


def test_currency_refuses_what_makes_no_sense(store):
    currency = tool(store, Sources(), Currency)
    with pytest.raises(ToolError, match="isn't a currency code"):
        asyncio.run(currency.convert_currency(1, "pounds", "EUR"))
    with pytest.raises(ToolError, match="more than zero"):
        asyncio.run(currency.convert_currency(0, "GBP", "EUR"))
    with pytest.raises(ToolError, match="No rate for GBP to ZZZ"):
        asyncio.run(currency.convert_currency(1, "GBP", "ZZZ"))
    assert asyncio.run(currency.convert_currency(5, "GBP", "gbp")) == "5 GBP is 5 GBP."


@pytest.mark.parametrize(
    ("expression", "answer"),
    [
        ("1234.5 * 1.2 / 3", "1234.5 * 1.2 / 3 = 493.8"),
        ("0.1 + 0.2", "0.1 + 0.2 = 0.3"),
        ("(48.60 + 12.90) / 3", "(48.60 + 12.90) / 3 = 20.5"),
        ("2 ** 10 - -1", "2 ** 10 - -1 = 1,025"),
        ("17 // 5 + 17 % 5", "17 // 5 + 17 % 5 = 5"),
    ],
)
def test_calculate_is_exact(store, expression, answer):
    assert asyncio.run(tool(store, Sources(), Dates).calculate(expression)) == answer


@pytest.mark.parametrize("expression", ["__import__('os')", "1 / 0", "2 ** 100000", "abs(-1)", "True + 1", "1 +", "x * 2"])
def test_calculate_does_nothing_but_arithmetic(store, expression):
    with pytest.raises(ToolError, match="Couldn't work out"):
        asyncio.run(tool(store, Sources(), Dates).calculate(expression))


def test_days_between_dates(store):
    dates = tool(store, Sources(), Dates)
    assert asyncio.run(dates.days_between("2026-11-17", "2026-12-25")) == "Tue 17 Nov 2026 to Fri 25 Dec 2026: 38 days (5 weeks and 3 days)."
    today = datetime.now(TIMEZONE).date()
    assert f"{today:%a %-d %b %Y} to {today:%a %-d %b %Y}: 0 days" in asyncio.run(dates.days_between(today.isoformat()))
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        asyncio.run(dates.days_between("Christmas"))


def test_bank_holidays_are_the_ones_still_to_come(store):
    events = [{"date": "2000-01-03", "title": "Long gone"}, {"date": "2999-12-27", "title": "Boxing Day (substitute day)"}, {"date": "2999-12-28", "title": "Later"}]
    sources = Sources(**{"bank-holidays.json": {"scotland": {"events": events}, "england-and-wales": {"events": events[:2]}}})
    dates = tool(store, sources, Dates)
    assert asyncio.run(dates.bank_holidays("scotland", count=1)) == "Fri 27 Dec 2999: Boxing Day (substitute day)"
    assert asyncio.run(dates.bank_holidays()).count("\n") == 0
    with pytest.raises(ToolError, match="regions"):
        asyncio.run(dates.bank_holidays("wales"))


def test_time_in_a_place_and_how_far_it_is(store):
    tokyo = {"results": [{"name": "Tokyo", "country": "Japan", "latitude": 35.7, "longitude": 139.7, "timezone": "Asia/Tokyo"}]}
    dates = tool(store, Sources(**{"v1__search": tokyo}), Dates)
    answer = asyncio.run(dates.time_in("Tokyo"))
    assert answer.startswith("Tokyo, Japan: ")
    assert "(Asia/Tokyo), 9 h ahead of the owner's." in answer or "(Asia/Tokyo), 8 h ahead" in answer  # London's summer
    with pytest.raises(ToolError, match="by name"):
        asyncio.run(dates.time_in("35.7,139.7"))


def test_every_tool_is_named_and_documented_for_the_orchestrator(store):
    names = set()
    for kind in BUILT_IN:
        assert kind.name and kind.title and kind.about
        for function in kind(httpx.AsyncClient(), dict).functions():
            assert function.__doc__, function.__name__
            assert function.__name__ not in names
            names.add(function.__name__)
    assert names == {
        "tfl_status", "tfl_journey", "tfl_arrivals", "weather", "convert_currency",
        "time_in", "bank_holidays", "days_between", "calculate",
    }


class Echo(Tool):
    name, title, about = "echo", "Echo", "says back what you say"
    settings = (Setting("key", "Key", secret=True, required=True), Setting("greeting", "Greeting"))

    def functions(self):
        return [self.echo]

    async def echo(self, words: str) -> str:
        """Says back what it's given."""
        if words == "boom":
            raise RuntimeError("the source blew up")
        if words == "no":
            raise ToolError("Echo won't say that.")
        return f"{self.setting('greeting') or 'Hello'} {words}"


def test_a_tool_is_on_by_default_unless_it_needs_a_setting(store):
    both = Tools(store, httpx.AsyncClient(), [Echo, TfL])
    assert [(t["name"], t["enabled"], t["active"]) for t in both.all()] == [("echo", True, False), ("tfl", True, True)]
    assert both.describe().startswith("TfL (")
    assert "Echo" not in both.describe()
    both.configure("echo", settings={"key": "secret"})
    assert both.describe().startswith("Echo (says back what you say); TfL")
    both.configure("echo", enabled=False)
    assert "Echo" not in both.describe()


def test_the_owner_sets_tools_and_their_secrets_never_leave(store):
    all_tools = Tools(store, httpx.AsyncClient(), [Echo])
    all_tools.configure("echo", settings={"key": " s3cret ", "greeting": "Hi"})
    all_tools.configure("echo", settings={"greeting": "Hey"})  # changing one leaves the other
    echo = all_tools.all()[0]
    assert echo["settings"] == [
        {"key": "key", "label": "Key", "secret": True, "required": True, "set": True, "value": ""},
        {"key": "greeting", "label": "Greeting", "secret": False, "required": False, "set": True, "value": "Hey"},
    ]
    assert "s3cret" not in json.dumps(all_tools.all())
    assert store.tools()["echo"]["settings"]["key"] == "s3cret"
    all_tools.configure("echo", settings={"greeting": ""})  # empty clears
    assert all_tools.all()[0]["settings"][1]["set"] is False
    assert all_tools.all()[0]["enabled"] is True
    with pytest.raises(ValueError, match="no setting colour"):
        all_tools.configure("echo", settings={"colour": "red"})
    with pytest.raises(KeyError):
        all_tools.configure("nothing")


def test_a_second_tool_cannot_take_a_name(store):
    class Again(Echo):
        name = "again"

    with pytest.raises(ValueError, match="Two quick tools are called echo"):
        Tools(store, httpx.AsyncClient(), [Echo, Again])


def drive(all_tools: Tools, *calls: tuple[str, dict]) -> tuple[list[str], list[str]]:
    """An agent that calls each of `calls`, then stops: what the tools offered it, and what each call returned."""
    offered: list[str] = []
    pending = list(calls)

    def model(messages, info: AgentInfo) -> ModelResponse:
        offered[:] = [t.name for t in info.function_tools]
        if pending:
            name, args = pending.pop(0)
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return ModelResponse(parts=[TextPart("done")])

    result = asyncio.run(Agent(FunctionModel(model), toolsets=[all_tools.toolset]).run("go"))
    returned = [p.content for m in result.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
    return offered, returned


def test_only_active_tools_are_offered_to_the_orchestrator(store):
    all_tools = Tools(store, httpx.AsyncClient(), [Echo, Dates])
    offered, _ = drive(all_tools)
    assert offered == ["time_in", "bank_holidays", "days_between", "calculate"]
    all_tools.configure("echo", settings={"key": "k"})
    assert drive(all_tools)[0][0] == "echo"
    all_tools.configure("dates", enabled=False)
    assert drive(all_tools)[0] == ["echo"]


def test_a_tool_that_fails_is_an_answer_not_an_error(store):
    all_tools = Tools(store, httpx.AsyncClient(), [Echo])
    all_tools.configure("echo", settings={"key": "k", "greeting": "Hi"})
    _, returned = drive(all_tools, ("echo", {"words": "there"}), ("echo", {"words": "no"}), ("echo", {"words": "boom"}))
    assert returned == [
        "Hi there",
        "Echo won't say that.",
        "Echo isn't answering (RuntimeError): try the web instead.",
    ]


def test_a_source_that_is_down_is_told_to_the_model_plainly(store):
    sources = Sources(**{"Line__Mode__tube,overground,dlr,elizabeth-line,tram__Status": 503})
    _, returned = drive(tools(store, sources, [TfL]), ("tfl_status", {}))
    assert returned == ["TfL isn't answering (HTTPStatusError): try the web instead."]


def test_the_email_senders_setting_is_a_quick_tool_with_no_functions_of_its_own(store):
    quick = Tools(store, httpx.AsyncClient(), [Inbox])
    assert quick.setting("inbox", "senders") == "" and quick.describe() == ""  # nothing set: no one is read
    quick.configure("inbox", settings={"senders": "Sam@Example.com, priya@example.net;\n<Tom@example.org>"})
    assert addresses(quick.setting("inbox", "senders")) == {"sam@example.com", "priya@example.net", "tom@example.org"}
    assert quick.describe() == ""  # nothing for the orchestrator to call
    quick.configure("inbox", enabled=False)
    assert quick.setting("inbox", "senders") == ""  # off reads no one
    assert addresses("") == frozenset()
