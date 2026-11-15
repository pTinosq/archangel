import asyncio
import base64
import json

import pytest

from bridge.executor import REMEMBERED_REPLIES, Executor
from fakes import FakeDevice


def run(device: FakeDevice, *frames) -> list[dict]:
    executor = Executor(device)

    async def go():
        return [await executor.execute(frame if isinstance(frame, str) else json.dumps(frame)) for frame in frames]

    return asyncio.run(go())


def test_tap_defaults_to_a_left_click():
    device = FakeDevice()
    assert run(device, {"id": "1", "type": "tap", "x": 10.4, "y": 20.6}) == [{"id": "1", "ok": True}]
    assert device.calls == ["tap 10,21 50ms left x1"]


def test_right_and_double_clicks():
    device = FakeDevice()
    run(
        device,
        {"id": "1", "type": "tap", "x": 1, "y": 2, "button": "right"},
        {"id": "2", "type": "tap", "x": 1, "y": 2, "count": 2},
    )
    assert device.calls == ["tap 1,2 50ms right x1", "tap 1,2 50ms left x2"]


def test_screenshot_returns_its_scaled_size_and_base64():
    [reply] = run(FakeDevice(), {"id": "1", "type": "screenshot", "scale": 0.5, "quality": 30})
    assert reply["data"] == {"width": 960, "height": 540, "jpeg": base64.b64encode(b"jpeg").decode()}


@pytest.mark.parametrize(
    "command",
    [
        {"type": "screenshot", "scale": 0},
        {"type": "screenshot", "scale": 1.5},
        {"type": "screenshot", "quality": 0},
        {"type": "tap", "x": 1},
        {"type": "tap", "x": 1, "y": 2, "button": "middle"},
        {"type": "tap", "x": 1, "y": 2, "count": 3},
        {"type": "scroll", "x": 1, "y": 2, "dy": 500},
        {"type": "type"},
        {"type": "location", "lat": 1, "lon": 2},  # Android's
        {"type": "wake", "mac": "aa:bb:cc:dd:ee:ff"},  # Android's
        {"type": "teleport"},
    ],
)
def test_bad_commands_are_invalid(command):
    [reply] = run(FakeDevice(), {"id": "1", **command})
    assert reply["ok"] is False and reply["error"]["code"] == "invalid_command", reply


def test_another_platforms_command_says_so():
    [reply] = run(FakeDevice(), {"id": "1", "type": "location"})
    assert reply["error"]["message"] == "Not a command on windows: location"


def test_a_validation_error_names_the_field():
    [reply] = run(FakeDevice(), {"id": "1", "type": "tap", "x": 1, "y": 2, "button": "middle"})
    assert reply["error"]["message"].startswith("button:")


@pytest.mark.parametrize("frame", ["not json", "[]", '{"type": "tap"}', '{"id": 5, "type": "tap"}'])
def test_frames_without_an_id_are_answered_too(frame):
    [reply] = run(FakeDevice(), frame)
    error = {"code": "invalid_command", "message": "Expected a JSON object with an id"}
    assert reply == {"id": "", "ok": False, "error": error}


def test_a_repeated_id_is_replayed_not_rerun():
    device = FakeDevice()
    first, again = run(device, {"id": "1", "type": "key", "key": "enter"}, {"id": "1", "type": "key", "key": "enter"})
    assert first == again
    assert device.calls == ["key enter"]


def test_only_the_latest_replies_are_remembered():
    device = FakeDevice()
    frames = [{"id": str(n), "type": "key", "key": "a"} for n in range(REMEMBERED_REPLIES + 1)]
    run(device, *frames, frames[0])
    assert len(device.calls) == REMEMBERED_REPLIES + 2  # the first was forgotten, so it ran again


def test_device_failures_keep_their_code():
    [reply] = run(FakeDevice(), {"id": "1", "type": "launch", "package": "nope"})
    assert reply["error"] == {"code": "not_found", "message": "No app with id 'nope'"}


def test_unexpected_failures_are_internal_and_never_raise():
    device = FakeDevice()

    async def broken(text):
        raise RuntimeError("boom")

    device.type = broken
    [reply] = run(device, {"id": "1", "type": "type", "text": "hi"})
    assert reply["error"] == {"code": "internal", "message": "RuntimeError: boom"}


def test_apps_and_ui_tree_return_data():
    apps, tree = run(FakeDevice(), {"id": "1", "type": "apps"}, {"id": "2", "type": "ui_tree"})
    assert apps["data"] == {"apps": [{"label": "Notepad", "package": "notepad"}]}
    assert tree["data"]["windows"][0]["class"] == "Window"


def test_commands_run_one_at_a_time():
    device = FakeDevice()
    running = []

    async def slow(text):
        running.append(text)
        assert len(running) == 1, "two at once"
        await asyncio.sleep(0.01)
        running.remove(text)

    device.type = slow
    executor = Executor(device)

    async def go():
        frames = [json.dumps({"id": str(n), "type": "type", "text": str(n)}) for n in range(3)]
        return await asyncio.gather(*(executor.execute(frame) for frame in frames))

    assert all(reply["ok"] for reply in asyncio.run(go()))
