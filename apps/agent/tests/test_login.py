import asyncio
import time

import httpx
from fastapi import FastAPI

from agent.channels import login
from agent.channels.login import SESSION, STATE, GitHub, protect

GITHUB = GitHub("id", "secret", "pTinosq")


def github(user: str | None = "ptinosq") -> httpx.AsyncClient:
    """GitHub, signing in `user` (None: the code is refused)."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url == login.TOKEN:
            assert b"code=good" in request.content
            return httpx.Response(200, json={"access_token": "t"} if user else {"error": "bad_verification_code"})
        assert request.headers["authorization"] == "Bearer t"
        return httpx.Response(200, json={"login": user})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def app(user: str | None = "ptinosq") -> FastAPI:
    app = FastAPI()
    protect(app, GITHUB, github(user))

    @app.get("/api/tasks")
    async def tasks() -> list:
        return []

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/")
    async def home() -> str:
        return "panel"

    return app


def call(app: FastAPI, url: str, cookies: dict | None = None) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="https://archangel", cookies=cookies) as client:
            return await client.get(url)

    return asyncio.run(go())


def sign_in(app: FastAPI) -> httpx.Response:
    state = call(app, "/auth/login").cookies[STATE]
    return call(app, f"/auth/callback?code=good&state={state}", {STATE: state})


def test_signed_out_is_sent_to_github():
    assert call(app(), "/api/tasks").status_code == 401
    assert call(app(), "/").headers["location"] == "/auth/login"
    assert call(app(), "/auth/login").headers["location"].startswith(login.AUTHORIZE + "?client_id=id&state=")
    assert call(app(), "/health").status_code == 200


def test_the_owner_gets_in():
    panel = app()
    signed_in = sign_in(panel)
    assert signed_in.headers["location"] == "/"
    session = {SESSION: signed_in.cookies[SESSION]}
    assert call(panel, "/api/tasks", session).json() == []
    assert call(panel, "/", session).json() == "panel"


def test_anyone_else_is_turned_away():
    assert sign_in(app("someone-else")).status_code == 403
    assert sign_in(app(None)).status_code == 400


def test_the_state_must_match():
    call(app(), "/auth/login")
    assert call(app(), "/auth/callback?code=good&state=forged", {STATE: "real"}).status_code == 400


def test_sessions_cant_be_forged_or_outlive_their_time(monkeypatch):
    panel = app()
    cookie = sign_in(panel).cookies[SESSION]
    name, expires, signature = cookie.split(".")
    assert call(panel, "/api/tasks", {SESSION: f"someone.{expires}.{signature}"}).status_code == 401
    assert call(panel, "/api/tasks", {SESSION: f"{name}.{int(expires) + 1}.{signature}"}).status_code == 401

    monkeypatch.setattr(time, "time", lambda: int(expires) + 1)
    assert call(panel, "/api/tasks", {SESSION: cookie}).status_code == 401
