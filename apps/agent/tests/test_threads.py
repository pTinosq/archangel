import asyncio
import sqlite3

import pytest
from fakes import FakeRouter, FakeSummariser
from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, RequestUsage, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent import activity
from agent.events import Notice, OwnerMessage, TaskUpdate
from agent.messages import Address, Message, Text
from agent.store import Status, Task
from agent.threads import FULL, QUIET, Route, Threads
from agent.threads.router import Choice, Router
from agent.threads.store import ThreadStore
from agent.threads.summariser import Summariser

SMS = Address("sms", "+44")


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def says(text: str) -> OwnerMessage:
    return OwnerMessage(Message(SMS, (Text(text),)))


def turn(said: str, answer: str, tokens: int = 100) -> list[ModelMessage]:
    return [
        ModelRequest.user_text_prompt(f"(via sms) {said}"),
        ModelResponse(parts=[TextPart(answer)], usage=RequestUsage(input_tokens=tokens)),
    ]


class World:
    def __init__(self, *choices: Choice, router=None):
        self.clock = Clock()
        self.store = ThreadStore(sqlite3.connect(":memory:"), self.clock)
        self.router = router or FakeRouter(*choices)
        self.summariser = FakeSummariser()
        self.threads = Threads(self.store, self.router, self.summariser, self.clock)

    def route(self, event) -> Route:
        return asyncio.run(self.threads.route(event))


def test_something_new_starts_a_thread_with_the_routers_title():
    world = World(Choice(thread=None, title="Flights to India"))
    route = world.route(says("can you look for flights to India?"))
    assert route.thread.title == "Flights to India" and route.thread.open
    assert world.router.heard == [("(via sms) can you look for flights to India?", [])]


def test_carrying_on_a_closed_thread_reopens_it():
    world = World()
    flights = world.store.new("Flights")
    world.store.close(flights.id)
    world.router.choices = [Choice(thread=flights.id)]
    route = world.route(says("for the flights, how much were they again?"))
    assert route.thread.id == flights.id and route.thread.open


def test_the_router_sees_the_recent_threads_newest_first():
    world = World()
    old, new = world.store.new("Old"), world.store.new("New")
    world.clock.now += 1
    world.threads.save(Route(old), turn("more on old", "ok"))
    world.route(says("hi"))
    assert world.router.heard[-1][1] == [old.id, new.id]


def test_if_the_router_fails_the_conversation_carries_on_where_it_was():
    class Broken:
        async def choose(self, said, recent):
            raise RuntimeError("model down")

    world = World(router=Broken())
    first = world.route(says("look for flights to India please"))
    assert first.thread.title == "look for flights to India please"  # no thread yet: one named by what was said
    assert world.route(says("and hotels")).thread.id == first.thread.id


def test_a_tasks_update_goes_back_to_the_thread_that_started_it():
    world = World()
    flights, birthday = world.store.new("Flights"), world.store.new("Birthday")
    task = Task(4, "Find flights to Delhi", SMS, Status.DONE, "From £540.", thread=flights.id)
    assert world.route(TaskUpdate(task)).thread.id == flights.id
    assert world.router.heard == []  # no model call: the task knows
    assert world.route(TaskUpdate(Task(5, "Old task", SMS, Status.DONE))).thread.id == birthday.id  # before threads


def test_an_update_to_a_closed_thread_leaves_it_closed_to_be_summarised_again():
    world = World()
    flights = world.store.new("Flights")
    world.store.summarise(flights.id, "Flights", "Looking for flights.", world.clock.now)
    world.clock.now += 1
    route = world.route(TaskUpdate(Task(4, "Find flights", SMS, Status.DONE, "£540.", thread=flights.id)))
    world.threads.save(route, turn("[Update on task #4] done: £540.", "Flights from £540."))
    assert not world.store.get(flights.id).open and world.store.get(flights.id).stale


def test_a_notice_goes_to_the_latest_thread():
    world = World()
    world.store.new("Old")
    latest = world.store.new("Latest")
    assert world.route(Notice("The call went to voicemail.", SMS)).thread.id == latest.id


def test_a_notice_with_no_thread_yet_starts_one():
    world = World()
    assert world.route(Notice("The call went to voicemail.", SMS)).thread.title == "The call went to voicemail."


def test_wrapping_up_closes_the_thread_and_summarises_it():
    world = World(Choice(thread=None, title="Flights"))

    async def scenario():
        route = await world.threads.route(says("flights to India?"))
        world.threads.save(route, turn("flights to India?", "From £540."))
        world.router.choices = [Choice(thread=route.thread.id, closes=True)]
        thanks = await world.threads.route(says("thanks!"))
        world.threads.save(thanks, route_history(world, route) + turn("thanks!", "Any time."))
        await world.threads.idle()
        return route.thread.id

    id = asyncio.run(scenario())
    thread = world.store.get(id)
    assert not thread.open and thread.summary == "4 lines about Flights." and thread.title == "Flights (summed up)"
    assert world.summariser.read == [["(via sms) flights to India?", "Archangel: From £540.",
                                      "(via sms) thanks!", "Archangel: Any time."]]  # fmt: skip


def route_history(world: World, route: Route) -> list[ModelMessage]:
    return world.store.history(route.thread.id)


def test_a_full_thread_carries_on_in_a_new_one_from_its_summary():
    world = World()
    flights = world.store.new("Flights")
    world.threads.save(Route(flights), turn("flights?", "£540.", tokens=FULL))
    world.router.choices = [Choice(thread=flights.id)]
    route = world.route(says("and the hotels?"))
    assert route.thread.id != flights.id and route.thread.continues == flights.id
    assert route.thread.title == "Flights" and route.thread.open
    full = world.store.get(flights.id)
    assert not full.open and full.summary == "2 lines about Flights."
    assert f'carries on #{flights.id}, "Flights (summed up)", which went:\n2 lines about Flights.' in (
        world.threads.describe(route.thread)
    )


def test_a_continuations_summary_includes_what_it_carried_on_from():
    world = World()
    flights = world.store.new("Flights")
    world.store.summarise(flights.id, "Flights", "Delhi from £540.", world.clock.now)
    more = world.store.new("Flights", continues=flights.id)

    async def scenario():
        world.threads.save(Route(more, closes=True), turn("book the £540 one", "Booked."))
        await world.threads.idle()

    asyncio.run(scenario())
    assert world.summariser.read[0][0] == '(Carrying on from "Flights": Delhi from £540.)'


def test_threads_left_quiet_are_summarised_and_closed():
    world = World()
    quiet, busy = world.store.new("Quiet"), world.store.new("Busy")
    world.threads.save(Route(quiet), turn("hello", "Hi."))
    world.clock.now += QUIET.total_seconds() + 1
    world.threads.save(Route(busy), turn("still here", "Yes."))
    asyncio.run(world.threads.tidy())
    assert not world.store.get(quiet.id).open and world.store.get(quiet.id).summary
    assert world.store.get(busy.id).open and not world.store.get(busy.id).summary


def test_a_failed_summary_is_tried_again_at_the_next_tidy():
    world = World()
    quiet = world.store.new("Quiet")
    world.clock.now += QUIET.total_seconds() + 1

    async def broken(thread, said):
        raise RuntimeError("model down")

    world.summariser.summarise = broken
    asyncio.run(world.threads.tidy())
    assert world.store.get(quiet.id).open
    world.summariser = FakeSummariser()
    world.threads._summariser = world.summariser
    asyncio.run(world.threads.tidy())
    assert not world.store.get(quiet.id).open


def test_the_orchestrator_is_told_its_thread_and_the_others():
    world = World()
    flights = world.store.new("Flights to India")
    world.store.close(flights.id)
    birthday = world.store.new("Text Anselm")
    described = world.threads.describe(birthday)
    assert described.startswith(f'You\'re in thread #{birthday.id}, "Text Anselm", started just now.')
    assert f'#{flights.id} "Flights to India" (closed, just now)' in described


def test_the_orchestrator_can_search_and_read_other_threads():
    world = World()
    flights = world.store.new("Flights to India")
    world.threads.save(Route(flights), turn("flights to Delhi?", "Return from £540."))
    tools = world.threads.tools.tools
    found = asyncio.run(tools["search_threads"].function("delhi"))
    assert found.startswith(f'#{flights.id} "Flights to India" · just now: ') and "[Delhi]" in found
    read = asyncio.run(tools["read_thread"].function(flights.id))
    assert "(via sms) flights to Delhi?\nArchangel: Return from £540." in read
    assert asyncio.run(tools["read_thread"].function(99)) == "There's no thread #99."


# The router and summariser on a model


def answering(**output) -> FunctionModel:
    """A model that answers with `output`, recording what it was shown."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        answering.seen = messages
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])

    return FunctionModel(respond)


def test_the_router_reads_summaries_and_how_unfinished_threads_end():
    store = ThreadStore(sqlite3.connect(":memory:"))
    flights, birthday = store.new("Flights to India"), store.new("Text Anselm")
    store.summarise(flights.id, "Flights to India", "Delhi from £540.", store.get(flights.id).updated + 1)
    store.save(birthday.id, turn("text Anselm happy birthday", "Which number?"), size=10)
    router = Router(answering(thread=birthday.id), store)
    choice = asyncio.run(router.choose("(via sms) the mobile", store.recent()))
    assert choice == Choice(thread=birthday.id)
    prompt = answering.seen[0].parts[-1].content
    assert prompt == (
        "Where the conversation is (Archangel's last words):\n"
        f'#{birthday.id} "Text Anselm" · open · just now\n'
        "  (via sms) text Anselm happy birthday\n"
        "  Archangel: Which number?\n\n"
        "Other recent threads, newest first:\n"
        f'#{flights.id} "Flights to India" · closed · just now\n'
        "  summary: Delhi from £540.\n\n"
        "The owner says (via sms) the mobile"
    )


@pytest.mark.parametrize(
    "wrong, why",
    [({"thread": 99}, "There's no thread #99"), ({"thread": None, "title": " "}, "A new thread needs a title")],
)
def test_the_router_is_told_to_fix_an_impossible_choice(wrong, why):
    store = ThreadStore(sqlite3.connect(":memory:"))
    answers = [wrong, {"thread": None, "title": "Flights"}]
    retries: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        retries.extend(str(part.content) for part in messages[-1].parts if part.part_kind == "retry-prompt")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answers.pop(0))])

    choice = asyncio.run(Router(FunctionModel(respond), store).choose("(via sms) flights?", []))
    assert choice.title == "Flights" and why in retries[0]


def test_the_router_can_search_every_thread():
    store = ThreadStore(sqlite3.connect(":memory:"))
    india = store.new("Flights to India")
    store.save(india.id, turn("flights to Delhi?", "£540."), size=10)
    calls = iter([ToolCallPart("search_threads", {"words": "delhi"}), None])

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if call := next(calls):
            return ModelResponse(parts=[call])
        found = messages[-1].parts[0].content
        assert f'#{india.id} "Flights to India"' in found
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"thread": india.id})])

    assert asyncio.run(Router(FunctionModel(respond), store).choose("(via sms) Delhi again?", [])).thread == india.id


def test_the_summariser_reads_the_thread():
    store = ThreadStore(sqlite3.connect(":memory:"))
    model = answering(title="Flights to India", summary="Delhi from £540.")
    summary = asyncio.run(Summariser(model).summarise(store.new("Flights"), ["(via sms) flights?", "Archangel: £540."]))
    assert (summary.title, summary.summary) == ("Flights to India", "Delhi from £540.")
    assert answering.seen[0].parts[-1].content == 'Thread "Flights":\n(via sms) flights?\nArchangel: £540.'


def test_a_thread_is_as_big_as_the_last_call_that_read_it():
    world = World()
    thread = world.store.new("Flights")
    world.threads.save(Route(thread), turn("a", "b", tokens=500) + turn("c", "d", tokens=900))
    assert world.store.get(thread.id).size == 900
    world.threads.save(Route(thread), [ModelRequest(parts=[UserPromptPart("no answer yet")])])
    assert world.store.get(thread.id).size == 0


def test_the_cockpit_sees_who_routes_and_who_summarises():
    world = World(Choice(thread=None, title="Flights", closes=True))

    async def scenario():
        with activity.acting("orchestrator"):
            route = await world.threads.route(says("flights? thanks"))
            world.threads.save(route, turn("flights? thanks", "£540."))
        await world.threads.idle()

    asyncio.run(scenario())
    calls = [(n.source, n.target, n.text) for n in activity._recent if n.dir == "tx"]
    assert calls == [
        ("orchestrator", "thread router", "(via sms) flights? thanks"),
        ("orchestrator", "thread summariser", "#1 Flights"),
    ]
