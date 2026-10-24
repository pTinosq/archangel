import asyncio

from pydantic_ai import ModelResponse, RetryPromptPart, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from agent.messages import Address, Message, Text
from agent.twin import QUIET, Twin
from agent.twin.graph import ROOT, Graph
from agent.twin.reader import Reader
from agent.twin.writer import Episode, Writer

SAID = "I hate morning flights"


def scripted(*calls: tuple[str, dict], note: str = "Noted: no morning flights.") -> FunctionModel:
    """Makes each call in turn (again after a retry), then ends with `note`."""

    def model(messages, info) -> ModelResponse:
        made = sum(isinstance(p, ToolCallPart) for m in messages for p in m.parts)
        if made < len(calls):
            return ModelResponse(parts=[ToolCallPart(*calls[made])])
        return ModelResponse(parts=[TextPart(note)])

    return FunctionModel(model)


def notice(writer: Writer, text: str = SAID) -> str:
    return asyncio.run(writer.notice(Episode(text, "chat")))


def test_what_lasts_goes_in_the_graph_with_the_owners_words(store):
    graph = Graph(store.db)
    writer = Writer(
        scripted(
            ("add", {"name": "Travel", "page": "", "said": SAID}),
            ("add", {"name": "Flights", "page": "Never mornings.", "said": SAID}),
            ("link", {"source": ROOT, "target": "Travel", "relation": "area", "preview": "how you get around", "said": SAID}),
            ("link", {"source": "Travel", "target": "Flights", "relation": "by", "preview": "never mornings", "said": SAID}),
        ),
        graph,
    )
    assert notice(writer) == "Noted: no morning flights."
    assert "→ Flights (by): never mornings" in graph.open("Travel")
    assert (graph.history("Flights")[-1].said, graph.history("Flights")[-1].origin) == (SAID, "chat")


def test_saying_it_noted_something_it_didnt_tells_no_one(store):
    assert notice(Writer(scripted(note="Noted: Indian food."), Graph(store.db)), "I fancy Indian tonight") == ""


def test_a_taken_name_goes_back_to_the_model_to_link_instead(store):
    graph = Graph(store.db)
    graph.add("Helen")
    graph.alias("Helen", "Mum")
    results = []

    def model(messages, info) -> ModelResponse:
        results.extend(p.content for p in messages[-1].parts if getattr(p, "tool_name", None) == "add")
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("add", {"name": "mum", "page": "", "said": "my mum"})])
        return ModelResponse(parts=[TextPart("Nothing.")])

    notice(Writer(FunctionModel(model), graph), "my mum")
    assert results == ["Refused: 'mum' is already Helen: link to it, or merge."]


def test_it_cant_finish_with_a_stale_preview(store):
    graph = Graph(store.db)
    graph.add("Flights")
    graph.link(ROOT, "Flights", "travels by", "aisle seat")
    retried = []

    def model(messages, info) -> ModelResponse:
        retried.extend(p for p in messages[-1].parts if isinstance(p, RetryPromptPart))
        calls = sum(isinstance(p, ToolCallPart) for m in messages for p in m.parts)
        if calls == 0:
            return ModelResponse(parts=[ToolCallPart("write", {"name": "Flights", "page": "Aisle. No mornings.", "said": SAID})])
        if retried and calls == 1:
            preview = {"source": ROOT, "target": "Flights", "relation": "travels by", "preview": "aisle, never mornings"}
            return ModelResponse(parts=[ToolCallPart("link", {**preview, "said": SAID})])
        return ModelResponse(parts=[TextPart("Noted: no morning flights.")])

    assert notice(Writer(FunctionModel(model), graph)) == "Noted: no morning flights."
    assert "You → Flights" in retried[0].model_response()
    assert graph.stale() == [] and graph.links_from(ROOT)[0].preview == "aisle, never mornings"


class FakeWriter:
    def __init__(self, note: str = "Noted: no morning flights."):
        self.read: list[Episode] = []
        self.note = note

    async def notice(self, episode: Episode) -> str:
        self.read.append(episode)
        return self.note


def twin(store, writer: FakeWriter) -> tuple[Twin, list]:
    told = []
    owners = {"sms": Address("sms", "+44"), "panel": Address("panel", "owner")}
    graph = Graph(store.db)
    found = Twin(store, graph, Reader(scripted(note="Nothing relevant."), graph), writer, owners.get)

    async def notify(event):
        told.append(event)

    found.notify = notify
    return found, told


def test_a_conversation_is_read_once_it_has_gone_quiet(store):
    store.add_line(Message(Address("panel", "owner"), (Text("before the twin"),)), "owner")
    writer = FakeWriter()
    found, told = twin(store, writer)
    store.add_line(Message(Address("voice", "CA1"), (Text(SAID),)), "owner")
    store.add_line(Message(Address("voice", "CA1"), (Text("Noted."),)), "archangel")
    last = store.lines()[-1].time

    asyncio.run(found.read_quiet(now=last + QUIET - 1))
    assert writer.read == []  # still talking

    asyncio.run(found.read_quiet(now=last + QUIET))
    asyncio.run(found.read_quiet(now=last + QUIET))  # and only once
    assert len(writer.read) == 1
    assert "Owner via voice: I hate morning flights" in writer.read[0].text
    assert "before the twin" not in writer.read[0].text
    assert "no morning flights" in told[0].text and told[0].reply_to == Address("sms", "+44")  # not ringing them


def test_only_archangel_talking_is_nothing_to_read(store):
    writer = FakeWriter()
    found, _ = twin(store, writer)
    store.add_line(Message(Address("panel", "owner"), (Text("Your task finished."),)), "archangel")
    asyncio.run(found.read_quiet(now=store.lines()[-1].time + QUIET))
    assert writer.read == []


def test_remember_is_written_at_once_and_not_announced(store):
    writer = FakeWriter()
    found, told = twin(store, writer)

    async def scenario():
        found.remember("remember my mum is Helen")
        await found.idle()

    asyncio.run(scenario())
    assert writer.read == [Episode("The owner asked: remember my mum is Helen", "chat")] and told == []


def uber_graph(store) -> Graph:
    graph = Graph(store.db)
    graph.write(ROOT, "- Call him Tinos.")
    graph.add("Uber", "- **Personal:** the cheapest ride.\n- Always 5 stars, never tip.")
    graph.link(ROOT, "Uber", "rides", "cheapest, 5 stars, no tip")
    return graph


def test_a_briefing_follows_previews_and_says_only_what_bears_on_the_task(store):
    graph, seen = uber_graph(store), []

    def model(messages, info) -> ModelResponse:
        seen.append(messages[-1].parts)
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("open", {"name": "uber"})])
        return ModelResponse(parts=[TextPart("Cheapest ride; rate 5 stars; no tip.")])

    briefing = asyncio.run(Reader(FunctionModel(model), graph).brief("Get me an Uber home"))
    assert briefing == "Cheapest ride; rate 5 stars; no tip."
    asked = seen[0][-1].content
    assert "Get me an Uber home" in asked and "→ Uber (rides): cheapest, 5 stars, no tip" in asked  # starts at the root
    assert "Always 5 stars, never tip." in seen[1][-1].content  # what it opened


def test_nothing_relevant_is_no_briefing(store):
    reader = Reader(scripted(note="Nothing relevant."), uber_graph(store))
    assert asyncio.run(reader.brief("What's the time in Tokyo?")) == ""
    assert asyncio.run(reader.ask("Favourite colour?")) == "Nothing relevant."


def test_any_agent_can_ask_the_twin_mid_task(store):
    graph = uber_graph(store)
    found = Twin(store, graph, Reader(scripted(note="Google."), graph), FakeWriter(), {}.get)
    ask = found.tools.tools["ask_twin"].function
    assert asyncio.run(ask("A sign-in page: which account?")) == "Google."
    assert "Call him Tinos." in found.root()


def test_a_failing_reader_leaves_the_task_unbriefed(store):
    class Broken(Reader):
        async def brief(self, goal):
            raise RuntimeError("429")

    graph = uber_graph(store)
    found = Twin(store, graph, Broken(scripted(), graph), FakeWriter(), {}.get)
    assert asyncio.run(found.brief("Uber home")) == ""
