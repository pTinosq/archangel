import base64
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from relay.app import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HELLO = {"type": "hello", "device": "pixel-6", "screen": {"width": 1080, "height": 2400, "density": 2.625}}


@pytest.fixture
def client():
    with TestClient(create_app(token=TOKEN, timeout=0.5)) as client:
        yield client


@contextmanager
def phone(client: TestClient, hello: dict = HELLO):
    with client.websocket_connect("/ws/device", headers=AUTH) as socket:
        socket.send_json(hello)
        wait_until_listed(client, hello["device"])
        yield socket


def wait_until_listed(client: TestClient, name: str) -> None:
    for _ in range(100):
        if any(d["device"] == name for d in client.get("/devices", headers=AUTH).json()):
            return
        time.sleep(0.01)
    raise AssertionError(f"{name} never connected")


def post(client: TestClient, command: dict) -> Future:
    return ThreadPoolExecutor(1).submit(client.post, "/devices/pixel-6/commands", json=command, headers=AUTH)


def test_health_needs_no_auth(client):
    assert client.get("/health").json() == {"ok": True}


def test_rejects_bad_token(client):
    assert client.get("/devices", headers={"Authorization": "Bearer nope"}).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/device", headers={"Authorization": "Bearer nope"}) as socket:
            socket.receive_json()


def test_lists_connected_devices(client):
    with phone(client):
        assert client.get("/devices", headers=AUTH).json() == [HELLO]
    assert client.get("/devices", headers=AUTH).json() == []


def test_command_round_trip(client):
    with phone(client) as socket:
        response = post(client, {"type": "tap", "x": 1, "y": 2})
        command = socket.receive_json()
        assert command == {"id": command["id"], "type": "tap", "x": 1, "y": 2}
        socket.send_json({"id": command["id"], "ok": True})
        assert response.result().json() == {"id": command["id"], "ok": True}


def test_keeps_caller_supplied_id(client):
    with phone(client) as socket:
        response = post(client, {"id": "abc", "type": "key", "key": "home"})
        assert socket.receive_json()["id"] == "abc"
        socket.send_json({"id": "abc", "ok": True})
        assert response.result().status_code == 200


def test_rejects_body_without_type(client):
    with phone(client):
        assert post(client, {"x": 1}).result().status_code == 422


def test_offline_device(client):
    response = post(client, {"type": "tap", "x": 1, "y": 2}).result()
    assert (response.status_code, response.json()["code"]) == (503, "offline")


def test_timeout(client):
    with phone(client) as socket:
        response = post(client, {"type": "tap", "x": 1, "y": 2})
        socket.receive_json()
        assert (response.result().status_code, response.result().json()["code"]) == (504, "timeout")


def test_disconnect_mid_command(client):
    with phone(client) as socket:
        response = post(client, {"type": "tap", "x": 1, "y": 2})
        socket.receive_json()
    assert (response.result().status_code, response.result().json()["code"]) == (502, "disconnected")


def test_screenshot_image(client):
    jpeg = b"\xff\xd8fake\xff\xd9"
    with phone(client) as socket:
        response = ThreadPoolExecutor(1).submit(client.get, "/devices/pixel-6/screenshot.jpg", headers=AUTH)
        command = socket.receive_json()
        assert command["type"] == "screenshot"
        data = {"width": 1, "height": 1, "jpeg": base64.b64encode(jpeg).decode()}
        socket.send_json({"id": command["id"], "ok": True, "data": data})
        assert response.result().headers["content-type"] == "image/jpeg"
        assert response.result().content == jpeg
