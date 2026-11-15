"""The bridge against the real relay: hello, commands and replies over the wire, reconnection."""

import asyncio
import contextlib
import socket

import httpx
import pytest
import uvicorn
from relay.app import create_app

from bridge import app
from bridge.config import Relay, Settings
from bridge.connection import backoff
from fakes import FakeDevice

TOKEN = "test-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def relay(port: int):
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN), host="127.0.0.1", port=port, log_level="warning"))
    running = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        server.should_exit = True
        await running


@contextlib.asynccontextmanager
async def bridge(port: int, device: FakeDevice, token: str = TOKEN, name: str = "desk"):
    settings = Settings(prod=Relay(f"ws://127.0.0.1:{port}/ws/device", token), device=name)
    running = asyncio.create_task(app.serve(settings, device))
    try:
        yield
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running


async def connected(client: httpx.AsyncClient, timeout: float = 5) -> list[dict]:
    for _ in range(int(timeout / 0.02)):
        if devices := (await client.get("/devices")).json():
            return devices
        await asyncio.sleep(0.02)
    raise AssertionError("never connected")


def client(port: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"})


def test_hello_and_commands_over_the_wire():
    port, device = free_port(), FakeDevice()

    async def go():
        async with relay(port), bridge(port, device), client(port) as http:
            [hello] = await connected(http)
            tap = await http.post("/devices/desk/commands", json={"type": "tap", "x": 5, "y": 6, "count": 2})
            shot = await http.get("/devices/desk/screenshot.jpg")
            wrong = await http.post("/devices/desk/commands", json={"type": "location", "lat": 1, "lon": 2})
            return hello, tap.json(), shot, wrong.json()

    hello, tap, shot, wrong = asyncio.run(go())
    assert hello == {
        "type": "hello",
        "device": "desk",
        "platform": "windows",
        "screen": {"width": 1920, "height": 1080, "density": 1.25},
        "version": "0.1.0",
        "wake": {"mac": "a8:a1:59:12:34:56"},
    }
    assert tap["ok"] and device.calls[0] == "tap 5,6 50ms left x2"
    assert shot.status_code == 200 and shot.content == b"jpeg"
    assert wrong["error"]["code"] == "invalid_command"


def test_a_wrong_token_never_connects():
    port = free_port()

    async def go():
        async with relay(port), bridge(port, FakeDevice(), token="wrong"), client(port) as http:
            await asyncio.sleep(0.5)
            return (await http.get("/devices")).json()

    assert asyncio.run(go()) == []


def test_it_reconnects_when_the_relay_comes_back(monkeypatch):
    monkeypatch.setattr("bridge.connection.backoff", lambda failures: 0.05)
    port = free_port()

    async def go():
        async with bridge(port, FakeDevice()):
            await asyncio.sleep(0.2)  # no relay yet: failing and retrying
            for _ in range(2):  # up, then down and up again
                async with relay(port), client(port) as http:
                    await connected(http)

    asyncio.run(go())


@pytest.mark.parametrize("failures, ceiling", [(1, 1), (2, 2), (3, 4), (6, 30), (50, 30)])
def test_backoff_doubles_to_30_seconds_with_jitter(failures, ceiling):
    delays = [backoff(failures) for _ in range(200)]
    assert all(ceiling * 0.5 <= delay <= ceiling for delay in delays)
