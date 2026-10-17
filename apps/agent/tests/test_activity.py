import asyncio

import pytest

from agent import activity


def hops():
    return [(n.source, n.target, n.dir, n.text, n.ok) for n in activity._recent]


def test_a_call_and_its_reply_nest_under_the_actor():
    with activity.acting("pilot"), activity.call("locator", "agent", "the Send button") as reply:
        activity.hop("jev", "model", "find it")
        reply("found")

    assert hops() == [
        ("pilot", "locator", "tx", "the Send button", True),
        ("locator", "jev", "tx", "find it", True),
        ("locator", "pilot", "rx", "found", True),
    ]


def test_a_failed_call_says_so():
    with pytest.raises(RuntimeError), activity.acting("pilot"), activity.call("phone", "phone", "tap"):
        raise RuntimeError

    assert hops()[-1] == ("phone", "pilot", "rx", "failed", False)


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
        activity.hop("b", "message", "x\n" * 500)
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
