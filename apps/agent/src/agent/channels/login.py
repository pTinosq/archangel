"""Sign in with GitHub, for the control panel when it's on the internet. One owner; everyone else is turned away.

A GitHub OAuth App, used directly: two redirects and a token exchange, no auth platform in between. The session is
a cookie signed with a key derived from the app's client secret, so there's no extra secret to keep; rotating the
client secret signs everyone out.
"""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

SESSION = "archangel_session"
STATE = "archangel_state"
SESSION_SECONDS = 30 * 24 * 3600
STATE_SECONDS = 600
OPEN = ("/auth/", "/health", "/twilio/")  # Twilio's routes check its signature instead
AUTHORIZE = "https://github.com/login/oauth/authorize"
TOKEN = "https://github.com/login/oauth/access_token"
USER = "https://api.github.com/user"


@dataclass(frozen=True)
class GitHub:
    client_id: str
    client_secret: str
    owner: str
    """The owner's GitHub username."""


def protect(app: FastAPI, github: GitHub, client: httpx.AsyncClient | None = None) -> None:
    """Add the sign-in routes, and refuse everything else to anyone but the owner."""
    client = client or httpx.AsyncClient(timeout=10)
    key = hmac.new(github.client_secret.encode(), b"session", hashlib.sha256).digest()

    @app.middleware("http")
    async def owner_only(request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith(OPEN) or _signed_in(request.cookies.get(SESSION), key, github.owner):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in first"}, status_code=401)
        return RedirectResponse("/auth/login")

    @app.get("/auth/login")
    async def login() -> Response:
        state = secrets.token_urlsafe(24)
        query = urlencode({"client_id": github.client_id, "state": state, "allow_signup": "false"})
        response = RedirectResponse(f"{AUTHORIZE}?{query}")
        response.set_cookie(STATE, state, STATE_SECONDS, path="/auth", secure=True, httponly=True, samesite="lax")
        return response

    @app.get("/auth/callback")
    async def callback(request: Request, code: str = "", state: str = "") -> Response:
        expected = request.cookies.get(STATE, "")
        if not (code and state and hmac.compare_digest(state, expected)):
            return _page("That sign-in link has expired.", 400)
        try:
            login = await _github_login(client, github, code)
        except httpx.HTTPError:
            return _page("Couldn't reach GitHub. Try again.", 502)
        if login is None:
            return _page("GitHub didn't sign you in.", 400)
        if login.lower() != github.owner.lower():
            return _page("This Archangel belongs to someone else.", 403)
        response = RedirectResponse("/")
        response.set_cookie(SESSION, _session(login, key), SESSION_SECONDS, secure=True, httponly=True, samesite="lax")
        response.delete_cookie(STATE, path="/auth")
        return response

    @app.get("/auth/logout")
    async def logout() -> Response:
        response = _page("Signed out.", 200)
        response.delete_cookie(SESSION)
        return response


async def _github_login(client: httpx.AsyncClient, github: GitHub, code: str) -> str | None:
    """The username of whoever GitHub just signed in, or None if the code didn't work."""
    form = {"client_id": github.client_id, "client_secret": github.client_secret, "code": code}
    token = (await client.post(TOKEN, data=form, headers={"Accept": "application/json"})).json().get("access_token")
    if not token:
        return None
    user = await client.get(USER, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    return user.json().get("login") if user.is_success else None


def _session(login: str, key: bytes) -> str:
    claim = f"{login}.{int(time.time()) + SESSION_SECONDS}"  # usernames have no dots
    return f"{claim}.{_sign(claim, key)}"


def _signed_in(cookie: str | None, key: bytes, owner: str) -> bool:
    parts = (cookie or "").split(".")
    if len(parts) != 3:
        return False
    login, expires, signature = parts
    return (
        hmac.compare_digest(signature, _sign(f"{login}.{expires}", key))
        and expires.isdigit()
        and int(expires) > time.time()
        and login.lower() == owner.lower()  # a new owner ends the old one's sessions
    )


def _sign(claim: str, key: bytes) -> str:
    return hmac.new(key, claim.encode(), hashlib.sha256).hexdigest()


def _page(text: str, status: int) -> HTMLResponse:
    body = f'<!doctype html><title>Archangel</title><p>{text}</p><p><a href="/auth/login">Sign in with GitHub</a></p>'
    return HTMLResponse(body, status)
