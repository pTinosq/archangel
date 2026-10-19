import asyncio

import pytest
from fakes import (
    FakeChannel,
    FakeConnectors,
    FakeHelper,
    FakePilot,
    FakeReporter,
    FakeResearcher,
    apps,
)
from pydantic_ai import (
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from agent import activity
from agent.gateway import Gateway
from agent.location import Whereabouts
from agent.orchestrator import Orchestrator
from agent.runs import Outcome
from agent.tasks import Tasks
from agent.web import Web


def hops():
    return [(n.source, n.target, n.dir, n.text, n.ok) for n in activity._recent]


def test_a_call_and_its_reply_nest_under_the_actor():
    with activity.acting("pilot"), activity.call("vision", "agent", "the Send button") as reply:
        activity.hop("jev", "model", "find it")
        reply("found")

    assert hops() == [
        ("pilot", "vision", "tx", "the Send button", True),
        ("vision", "jev", "tx", "find it", True),
        ("vision", "pilot", "rx", "found", True),
    ]


def test_a_failed_call_says_so():
    with pytest.raises(RuntimeError), activity.acting("pilot"), activity.call("phone", "phone", "tap"):
        raise RuntimeError("offline")

    assert hops()[-1] == ("phone", "pilot", "rx", "RuntimeError: offline", False)


def test_ids_follow_a_request_into_its_tasks():
    async def task():
        activity.hop("phone", "phone", "tap")

    async def request():
        activity.hop("orchestrator", "message", "hi")
        await asyncio.create_task(task(), context=activity.in_task(7, "tasks"))

    async def main():
        await asyncio.create_task(request(), context=activity.new_request("gateway"))
        activity.hop("cli", "message", "unrelated")

    asyncio.run(main())

    message, tap, unrelated = activity._recent
    assert (message.source, message.request, message.task) == ("gateway", tap.request, None)
    assert (tap.source, tap.task) == ("tasks", 7)
    assert message.request is not None and unrelated.request is None


def test_listeners_get_recent_then_live_and_text_is_clipped():
    activity.hop("a", "message", "earlier")

    async def main():
        stream = activity.listen()
        assert (await anext(stream)).text == "earlier"
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        activity.hop("b", "message", "x\n" * activity.MAX_TEXT)
        live = await pending
        await stream.aclose()
        return live

    live = asyncio.run(main())
    assert live.text.startswith("x x") and len(live.text) == activity.MAX_TEXT + 1
    assert not activity._listeners


def test_a_listener_that_falls_behind_misses_notices_and_holds_nothing_up():
    async def main():
        stream = activity.listen()
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        for n in range(activity.QUEUED_PER_LISTENER + 50):
            activity.hop("a", "message", n)
        first = await pending
        await stream.aclose()
        return first

    assert asyncio.run(main()).text == "0"


def test_one_request_can_be_followed_through_its_task_and_back(store):
    def model(messages, info) -> ModelResponse:
        last = messages[-1].parts[-1]
        if isinstance(last, UserPromptPart) and "(via cli)" in last.content:
            return ModelResponse(parts=[ToolCallPart("start_task", {"goal": "Open WhatsApp"})])
        return ModelResponse(parts=[TextPart("On it." if isinstance(last, ToolReturnPart) else "Done.")])

    cli = FakeChannel("cli", ["open whatsapp"])
    gateway = Gateway([cli], store)
    tasks = Tasks(store, FakePilot(Outcome("done", "WhatsApp is open.")), FakeResearcher(), FakeReporter(), FakeHelper())
    orchestrator = Orchestrator(
        FunctionModel(model),
        store,
        tasks,
        gateway,
        FakeResearcher(),
        FakeConnectors(),
        apps,
        Whereabouts(store, Web(), None),
    )

    async def main():
        await gateway.run(orchestrator.handle)
        await tasks.idle()

    asyncio.run(main())

    notices = list(activity._recent)
    edges = {(n.source, n.target) for n in notices}
    assert {
        ("cli", "gateway"),
        ("gateway", "orchestrator"),
        ("orchestrator", "tasks"),
        ("tasks", "pilot"),
        ("pilot", "tasks"),
        ("tasks", "orchestrator"),
        ("orchestrator", "gateway"),
        ("gateway", "cli"),
    } <= edges
    assert {n.request for n in notices} == {notices[0].request} != {None}
    assert all(n.task == 1 for n in notices if "pilot" in (n.source, n.target))
    assert [n.text for n in notices if n.target == "cli"] == ["On it.", "Done."]
    roles = {n.source: n.source_role for n in notices} | {n.target: n.target_role for n in notices}
    assert (roles["cli"], roles["gateway"], roles["orchestrator"], roles["pilot"]) == (
        "channel",
        "part",
        "agent",
        "agent",
    )
