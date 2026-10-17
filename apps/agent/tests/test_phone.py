import asyncio
import base64
import json

import httpx
import pytest

from agent.messages import Image
from agent.phone import relay
from agent.phone.relay import Phone, PhoneError
from agent.phone.vision import Box, JevLocator

HELLO = [{"device": "pixel-6", "screen": {"width": 1000, "height": 2000}}]


def phone(handler) -> Phone:
    return Phone(httpx.AsyncClient(base_url="http://relay", transport=httpx.MockTransport(handler)), "pixel-6")


def test_tap_converts_fractions_to_pixels():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices":
            return httpx.Response(200, json=HELLO)
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "1", "ok": True})

    asyncio.run(phone(handler).tap(0.25, 0.5))
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
def test_failures_become_phone_errors(response, message):
    with pytest.raises(PhoneError, match=message):
        asyncio.run(phone(lambda _: response).type("hi"))


OFFLINE = httpx.Response(503, json={"code": "offline", "message": "Device is not connected"})
DONE = httpx.Response(200, json={"id": "1", "ok": True})


def test_commands_wait_for_an_offline_phone(monkeypatch):
    monkeypatch.setattr(relay, "POLL_SECONDS", 0)
    replies = [OFFLINE, DONE]
    devices = [[], HELLO]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices":
            return httpx.Response(200, json=devices.pop(0))
        return replies.pop(0)

    asyncio.run(phone(handler).type("hi"))
    assert replies == devices == []


def test_a_phone_that_stays_offline_fails_the_command(monkeypatch):
    monkeypatch.setattr(relay, "OFFLINE_WAIT_SECONDS", 0)
    with pytest.raises(PhoneError, match="offline for 0 s"):
        asyncio.run(phone(lambda _: OFFLINE).type("hi"))


def test_only_read_only_commands_are_resent_after_a_lost_connection():
    lost = httpx.Response(502, json={"code": "disconnected", "message": "Device disconnected"})
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["type"])
        return lost if len(sent) == 1 else httpx.Response(200, json={"id": "1", "ok": True, "data": {"apps": []}})

    asyncio.run(phone(handler).apps())
    assert sent == ["apps", "apps"]
    with pytest.raises(PhoneError, match="Device disconnected"):
        asyncio.run(phone(lambda _: lost).type("hi"))


def unnamed(devices: list, sent: list) -> Phone:
    """A phone with no name configured, behind a relay with `devices` connected."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices":
            return httpx.Response(200, json=devices)
        sent.append(request.url.path)
        return DONE

    return Phone(httpx.AsyncClient(base_url="http://relay", transport=httpx.MockTransport(handler)))


def test_without_a_name_it_uses_the_one_phone_connected():
    sent = []
    asyncio.run(unnamed([{"device": "galaxy-s30", "screen": {}}], sent).type("hi"))
    assert sent == ["/devices/galaxy-s30/commands"]


def test_without_a_name_several_phones_are_ambiguous():
    phones = [{"device": "a", "screen": {}}, {"device": "b", "screen": {}}]
    with pytest.raises(PhoneError, match=r"Several phones are connected \(a, b\); set ARCHANGEL_DEVICE"):
        asyncio.run(unnamed(phones, []).type("hi"))


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


def jev(answers: dict, requests: list) -> JevLocator:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"answers": answers})

    return JevLocator(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "typesafe/jev-1.14")


SCREEN = Image(b"jpg", "image/jpeg")
BOX = {"type": "box", "box": {"top": 0.18, "bottom": 0.26, "left": 0.31, "right": 0.46}, "confidence": 0.72}


def test_jev_finds_a_box():
    requests = []
    box = asyncio.run(jev({"visible": {"noul": 0.9}, "box": BOX}, requests).locate(SCREEN, "the WhatsApp icon"))

    assert box == Box(left=0.31, top=0.18, right=0.46, bottom=0.26)
    assert box.center == pytest.approx((0.385, 0.22))
    body = json.loads(requests[0].content)
    assert requests[0].headers["TypeSafe-Danger-Zone-Images"] == "true"
    assert body["state"]["screen"]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"jpg").decode()
    assert body["questions"]["box"] == {"type": "box", "instructions": "the WhatsApp icon in `screen`"}


def test_jev_reports_missing_targets():
    box = asyncio.run(jev({"visible": {"noul": 0.1}, "box": BOX}, []).locate(SCREEN, "a unicorn"))
    assert box is None
