import asyncio
import base64
import json

import httpx
import pytest

from agent.devices import relay
from agent.devices.relay import Device, DeviceError
from agent.devices.vision import Box, JevVision
from agent.messages import Image

HELLO = [{"device": "pixel-6", "screen": {"width": 1000, "height": 2000}}]
PC_HELLO = {
    "device": "desk",
    "platform": "windows",
    "screen": {"width": 3200, "height": 1800},
    "wake": {"mac": "a8:a1:59:12:34:56"},
}
DONE = httpx.Response(200, json={"id": "1", "ok": True})
OFFLINE = httpx.Response(503, json={"code": "offline", "message": "Device is not connected"})


def relay_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://relay", transport=httpx.MockTransport(handler))


def phone(handler, devices=HELLO) -> Device:
    """The phone, behind a relay where `devices` are connected and `handler` answers commands."""

    def route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=devices) if request.url.path == "/devices" else handler(request)

    return Device(relay_client(route), "android")


def test_tap_converts_fractions_to_pixels():
    sent = []
    asyncio.run(phone(lambda request: sent.append(json.loads(request.content)) or DONE).tap(0.25, 0.5))
    assert sent == [{"type": "tap", "x": 250, "y": 1000, "durationMs": 50}]


def test_screenshot_is_small_and_decoded():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"type": "screenshot", "scale": 0.5, "quality": 30}
        data = {"width": 540, "height": 1200, "jpeg": base64.b64encode(b"jpg").decode()}
        return httpx.Response(200, json={"id": "1", "ok": True, "data": data})

    assert asyncio.run(phone(handler).screenshot()) == Image(b"jpg", "image/jpeg")


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(504, json={"code": "timeout", "message": "Device did not reply"}), "Device did not reply"),
        (
            httpx.Response(
                200, json={"id": "1", "ok": False, "error": {"code": "not_found", "message": "No focused input field"}}
            ),
            "No focused input field",
        ),
    ],
)
def test_failures_become_device_errors(response, message):
    with pytest.raises(DeviceError, match=message):
        asyncio.run(phone(lambda _: response).type("hi"))


def test_commands_wait_for_an_offline_phone(monkeypatch):
    monkeypatch.setattr(relay, "POLL_SECONDS", 0)
    replies = [OFFLINE, DONE]
    devices = [HELLO, [], HELLO]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices":
            return httpx.Response(200, json=devices.pop(0))
        return replies.pop(0)

    asyncio.run(Device(relay_client(handler)).type("hi"))
    assert replies == devices == []


def test_a_phone_that_stays_offline_fails_the_command(monkeypatch):
    monkeypatch.setattr(relay, "OFFLINE_WAIT_SECONDS", 0)
    with pytest.raises(DeviceError, match="The phone has been offline for 0 s"):
        asyncio.run(phone(lambda _: OFFLINE).type("hi"))


def test_only_read_only_commands_are_resent_after_a_lost_connection():
    lost = httpx.Response(502, json={"code": "disconnected", "message": "Device disconnected"})
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["type"])
        return lost if len(sent) == 1 else httpx.Response(200, json={"id": "1", "ok": True, "data": {"apps": []}})

    asyncio.run(phone(handler).apps())
    assert sent == ["apps", "apps"]
    with pytest.raises(DeviceError, match="Device disconnected"):
        asyncio.run(phone(lambda _: lost).type("hi"))


def sent_to(device: Device, devices: list) -> list[str]:
    """Where `device` sends a command, behind a relay with `devices` connected."""
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices":
            return httpx.Response(200, json=devices)
        sent.append(request.url.path)
        return DONE

    device._client = relay_client(handler)
    asyncio.run(device.type("hi"))
    return sent


def test_it_uses_the_one_device_of_its_platform():
    connected = [{"device": "galaxy-s30", "screen": {}}, PC_HELLO]  # no platform: a phone from before there was one
    assert sent_to(Device(None, "android"), connected) == ["/devices/galaxy-s30/commands"]
    assert sent_to(Device(None, "windows"), connected) == ["/devices/desk/commands"]


def test_several_of_a_platform_are_ambiguous_unless_named():
    phones = [{"device": "a", "screen": {}}, {"device": "b", "screen": {}}]
    with pytest.raises(DeviceError, match=r"Several phones are connected \(a, b\); set ARCHANGEL_DEVICE"):
        sent_to(Device(None, "android"), phones)
    assert sent_to(Device(None, "android", ["b", "desk"]), phones) == ["/devices/b/commands"]


def test_taps_follow_the_latest_screenshot_when_the_phone_rotates():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        command = json.loads(request.content)
        sent.append(command)
        landscape = {"width": 1000, "height": 500, "jpeg": ""}  # half scale: the screen is 2000 × 1000
        return httpx.Response(
            200, json={"id": "1", "ok": True, "data": landscape if command["type"] == "screenshot" else None}
        )

    async def scenario():
        p = phone(handler)
        await p.screenshot()
        await p.tap(0.5, 0.5)

    asyncio.run(scenario())
    assert sent[-1] == {"type": "tap", "x": 1000, "y": 500, "durationMs": 50}


def pc(handler, devices=(PC_HELLO,), memory=None, wake=None) -> Device:
    def route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=list(devices)) if request.url.path == "/devices" else handler(request)

    return Device(relay_client(route), "windows", memory=memory, wake=wake)


def test_a_pc_screenshot_is_shrunk_to_a_readable_width_not_halved():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        data = {"width": 1600, "height": 900, "jpeg": ""}
        return httpx.Response(200, json={"id": "1", "ok": True, "data": data})

    asyncio.run(pc(handler).screenshot())
    small = {**PC_HELLO, "screen": {"width": 1280, "height": 720}}
    asyncio.run(pc(handler, [small]).screenshot())
    assert sent == [
        {"type": "screenshot", "scale": 0.5, "quality": 60},
        {"type": "screenshot", "scale": 1.0, "quality": 60},
    ]


def test_clicks_scrolls_and_unlock_go_in_pixels():
    sent = []
    device = pc(lambda request: sent.append(json.loads(request.content)) or DONE)

    async def scenario():
        await device.click(0.5, 0.25, button="right")
        await device.click(0.5, 0.25, double=True)
        await device.scroll(0.5, 0.5, dy=5)
        await device.unlock()

    asyncio.run(scenario())
    assert sent == [
        {"type": "tap", "x": 1600, "y": 450, "button": "right", "count": 1},
        {"type": "tap", "x": 1600, "y": 450, "button": "left", "count": 2},
        {"type": "scroll", "x": 1600, "y": 900, "dx": 0, "dy": 5},
        {"type": "unlock"},
    ]


class Memory:
    def __init__(self, last=None):
        self.saved, self.last = [], last

    def save_device(self, hello):
        self.saved.append(hello)

    def last_device(self, platform):
        return self.last


def test_a_device_is_remembered_once_per_change():
    memory = Memory()
    device = pc(lambda _: DONE, memory=memory)

    async def scenario():
        await device.type("a")
        await device.state()

    asyncio.run(scenario())
    assert memory.saved == [PC_HELLO]


def test_a_sleeping_pc_is_woken_by_the_phone_and_waited_for(monkeypatch):
    monkeypatch.setattr(relay, "POLL_SECONDS", 0)
    woken = []
    connected = [[], [], [PC_HELLO], [PC_HELLO]]
    replies = [DONE]

    async def wake(mac):
        woken.append(mac)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices":
            return httpx.Response(200, json=connected.pop(0))
        return replies.pop(0)

    device = Device(relay_client(handler), "windows", memory=Memory(PC_HELLO), wake=wake)
    asyncio.run(device.type("hi"))
    assert woken == ["a8:a1:59:12:34:56"]
    assert replies == []


def test_a_pc_that_wont_wake_says_so(monkeypatch):
    monkeypatch.setattr(relay, "WAKE_WAIT_SECONDS", 0)

    async def wake(mac):
        raise DeviceError("The phone is not connected")

    device = pc(lambda _: OFFLINE, devices=(), memory=Memory(PC_HELLO), wake=wake)
    with pytest.raises(DeviceError, match="The PC has been offline for 0 s .*Wake-on-LAN"):
        asyncio.run(device.type("hi"))


def test_state_says_online_offline_or_never():
    async def wake(mac):
        pass

    assert asyncio.run(pc(lambda _: DONE).state()) == "online"
    asleep = pc(lambda _: DONE, devices=(), memory=Memory(PC_HELLO), wake=wake)
    assert asyncio.run(asleep.state()) == "offline (it's woken when a task needs it)"
    assert asyncio.run(pc(lambda _: DONE, devices=(), memory=Memory(PC_HELLO)).state()) == "offline"
    assert asyncio.run(pc(lambda _: DONE, devices=(), memory=Memory()).state()) is None


def jev(answers: dict, requests: list) -> JevVision:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"answers": answers})

    return JevVision(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "typesafe/jev-1.14")


SCREEN = Image(b"jpg", "image/jpeg")
BOX = {"type": "box", "box": {"top": 0.18, "bottom": 0.26, "left": 0.31, "right": 0.46}, "confidence": 0.72}


def test_jev_finds_a_box():
    requests = []
    box = asyncio.run(jev({"visible": {"noul": 0.9}, "box": BOX}, requests).find(SCREEN, "the WhatsApp icon"))

    assert box == Box(left=0.31, top=0.18, right=0.46, bottom=0.26)
    assert box.center == pytest.approx((0.385, 0.22))
    body = json.loads(requests[0].content)
    assert requests[0].headers["TypeSafe-Danger-Zone-Images"] == "true"
    assert body["state"]["screen"]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"jpg").decode()
    assert body["questions"]["box"] == {"type": "box", "instructions": "the WhatsApp icon in `screen`"}


def test_jev_reports_missing_targets():
    box = asyncio.run(jev({"visible": {"noul": 0.1}, "box": BOX}, []).find(SCREEN, "a unicorn"))
    assert box is None
