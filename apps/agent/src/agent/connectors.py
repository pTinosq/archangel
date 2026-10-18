"""The owner's online services (Gmail and Calendar, Notion, Slack, any other MCP server), for the helper to use
without the phone. Added and signed in to from the panel's Connectors page; the sign-ins live in the store.

Each is an MCP server. Hosted ones (Notion, Slack, …) sit behind OAuth: the MCP SDK signs in (registering Archangel
itself where the server allows; Slack wants the owner's own Slack app) and refreshes the tokens. Google's own are
invite-only, so Gmail and Calendar run here (workspace-mcp, a subprocess) on a Google sign-in done by Archangel.

Read-only tools run freely; anything else (send, write, delete) waits for the owner's approval.
"""

import asyncio
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastmcp.client.transports import StdioTransport
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic_ai import ApprovalRequired, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from agent.store import Store

log = logging.getLogger(__name__)

Kind = Literal["google", "notion", "slack", "mcp"]
SIGN_IN_SECONDS = 600
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USER = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.modify",  # read, label, draft, send; not delete forever
    "https://www.googleapis.com/auth/calendar",
]
WORKSPACE_MCP = ["workspace-mcp@2.1.0", "--single-user", "--tools", "gmail", "calendar", "--tool-tier", "extended"]
WORKSPACE_MCP_OFF = ["list_gmail_filters", "manage_gmail_filter"]  # need a scope not asked for


@dataclass(frozen=True)
class Service:
    """One the Connectors page offers."""

    kind: Kind
    title: str
    url: str = ""
    """The MCP server; Google's runs here, and other MCP servers are given by the owner."""
    needs: tuple[str, ...] = ()
    """What the owner enters to set it up."""
    help: str = ""


SERVICES = [
    Service(
        "google",
        "Gmail and Calendar",
        needs=("client_id", "client_secret"),
        help="A Google Cloud OAuth client (web application) with this redirect URI: {callback}",
    ),
    Service("notion", "Notion", "https://mcp.notion.com/mcp"),
    Service(
        "slack",
        "Slack",
        "https://mcp.slack.com/mcp",
        needs=("client_id", "client_secret"),
        help="An internal Slack app in your workspace with this redirect URL: {callback}",
    ),
    Service("mcp", "Another MCP server", needs=("url",), help="Its address, e.g. https://mcp.example.com/mcp"),
]


@dataclass
class Connector:
    name: str
    kind: Kind
    title: str
    url: str = ""
    settings: dict[str, str] = field(default_factory=dict)
    """What the owner entered: for some, a client id and secret."""
    tokens: dict[str, Any] | None = None
    client: dict[str, Any] | None = None
    """The client registered with the server (OAuth dynamic registration)."""
    account: str = ""
    """Who it's signed in as, where the service says (Google)."""
    signed_in: bool = False


class Connectors:
    def __init__(self, store: Store, site: str, directory: Path, client: httpx.AsyncClient | None = None):
        """`site`: Archangel's web address, which services send the owner back to after signing in. `directory`:
        where the Google connector keeps its credentials."""
        self._store = store
        self._callback = f"{site}/connectors/callback"
        self._google = directory / "google"
        self._http = client or httpx.AsyncClient(timeout=30)
        self._signing_in: dict[str, _SignIn] = {}  # by OAuth state

    def services(self) -> list[dict]:
        return [
            {"kind": s.kind, "title": s.title, "needs": s.needs, "help": s.help.format(callback=self._callback)}
            for s in SERVICES
        ]

    def all(self) -> list[Connector]:
        return [_connector(data) for data in self._store.connectors().values()]

    def connected(self) -> list[Connector]:
        return [connector for connector in self.all() if connector.signed_in]

    def add(self, kind: Kind, settings: dict[str, str]) -> Connector:
        """Add or replace a connector; the owner signs in next."""
        service = next(s for s in SERVICES if s.kind == kind)
        if missing := [need for need in service.needs if not settings.get(need, "").strip()]:
            raise ValueError(f"{service.title} needs {', '.join(missing)}")
        url = settings.pop("url", "").strip() or service.url
        title = (urlparse(url).hostname or url) if kind == "mcp" else service.title
        name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") if kind == "mcp" else kind
        connector = Connector(name, kind, title, url, {k: v.strip() for k, v in settings.items()})
        self._save(connector)
        return connector

    def remove(self, name: str) -> None:
        self._store.save_connector(name, None)

    async def sign_in(self, name: str) -> str | None:
        """Where the owner signs in to `name`; None if they already are (its sign-in still works)."""
        connector = self._get(name)
        state = secrets.token_urlsafe(24)
        if connector.kind == "google":
            self._signing_in[state] = _SignIn(name)
            return f"{GOOGLE_AUTHORIZE}?" + urlencode(
                {
                    "client_id": connector.settings["client_id"],
                    "redirect_uri": self._callback,
                    "response_type": "code",
                    "scope": " ".join(GOOGLE_SCOPES),
                    "access_type": "offline",
                    "prompt": "consent",  # a refresh token every time, even on reconnecting
                    "state": state,
                }
            )
        # The SDK runs the whole sign-in itself, on connecting: hand the owner its link, and it the code they bring.
        link, code = asyncio.get_running_loop().create_future(), asyncio.get_running_loop().create_future()
        auth = self._oauth(connector, redirect=lambda url: _set(link, url), code=code)
        connecting = asyncio.create_task(self._connect(connector, auth))
        await asyncio.wait([link, connecting], return_when=asyncio.FIRST_COMPLETED)
        if connecting.done():
            connecting.result()  # raises what went wrong
            self._signed_in(name)
            return None
        self._signing_in[_state(link.result())] = _SignIn(name, code, connecting)
        return link.result()

    async def finish(self, state: str, code: str) -> Connector:
        """The owner is back from signing in, with `code`."""
        if not (signing_in := self._signing_in.pop(state, None)) or signing_in.expired:
            raise ValueError("That sign-in has expired. Try again from Connectors.")
        connector = self._get(signing_in.name)
        if connector.kind == "google":
            await self._google_tokens(connector, code)
        else:
            signing_in.code.set_result(AuthorizationCodeResult(code=code, state=state))
            await signing_in.connecting
            connector = self._get(connector.name)  # tokens saved meanwhile
        self._save(connector)
        return self._signed_in(connector.name)

    def toolsets(self, mark: Callable[[str], None]) -> list[AbstractToolset]:
        """Every signed-in connector's tools, read-only ones free, the rest needing approval; `mark` hears each
        call, for the task's steps."""
        return [
            _Guarded(self._toolset(connector), connector, mark, self._signed_out).prefixed(connector.name)
            for connector in self.connected()
        ]

    def _toolset(self, connector: Connector) -> MCPToolset:
        if connector.kind == "google":
            return MCPToolset(self._workspace(connector), id=connector.name)
        return MCPToolset(connector.url, id=connector.name, auth=self._oauth(connector))

    def _workspace(self, connector: Connector) -> StdioTransport:
        # workspace-mcp reads (and refreshes) the sign-in from a file of its own; ours, in the store, is the original.
        self._google.mkdir(parents=True, exist_ok=True)
        tokens = connector.tokens or {}
        credentials = {
            "token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "token_uri": GOOGLE_TOKEN,
            "client_id": connector.settings["client_id"],
            "client_secret": connector.settings["client_secret"],
            "scopes": GOOGLE_SCOPES,
        }
        (self._google / f"{connector.account}.json").write_text(json.dumps(credentials))
        env = {
            "GOOGLE_OAUTH_CLIENT_ID": connector.settings["client_id"],
            "GOOGLE_OAUTH_CLIENT_SECRET": connector.settings["client_secret"],
            "WORKSPACE_MCP_CREDENTIALS_DIR": str(self._google.resolve()),
            "USER_GOOGLE_EMAIL": connector.account,
            "WORKSPACE_MCP_DISABLED_TOOLS": ",".join(WORKSPACE_MCP_OFF),
        }
        return StdioTransport("uvx", WORKSPACE_MCP, env=env)

    async def _google_tokens(self, connector: Connector, code: str) -> None:
        response = await self._http.post(
            GOOGLE_TOKEN,
            data={
                "code": code,
                "client_id": connector.settings["client_id"],
                "client_secret": connector.settings["client_secret"],
                "redirect_uri": self._callback,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        connector.tokens = response.json()
        user = await self._http.get(GOOGLE_USER, headers={"Authorization": f"Bearer {connector.tokens['access_token']}"})
        user.raise_for_status()
        connector.account = user.json()["email"]

    def _oauth(
        self,
        connector: Connector,
        redirect: Callable[[str], None] | None = None,
        code: asyncio.Future | None = None,
    ) -> OAuthClientProvider:
        """Signs in to a hosted server. Without `redirect` and `code`, only uses (and refreshes) the sign-in it has:
        one that has stopped working fails, and the owner signs in again from the panel."""

        async def redirected(url: str) -> None:
            redirect(url)

        async def came_back() -> AuthorizationCodeResult:
            return await asyncio.wait_for(code, SIGN_IN_SECONDS)

        return OAuthClientProvider(
            connector.url,
            OAuthClientMetadata(
                client_name="Archangel",
                redirect_uris=[self._callback],
                grant_types=["authorization_code", "refresh_token"],
                token_endpoint_auth_method="client_secret_post" if "client_secret" in connector.settings else "none",
            ),
            _Storage(self, connector.name),
            redirected if redirect else None,
            came_back if code else None,
        )

    async def _connect(self, connector: Connector, auth: OAuthClientProvider) -> None:
        async with MCPToolset(connector.url, auth=auth):  # connecting is what signs in
            pass

    def _signed_in(self, name: str) -> Connector:
        connector = self._get(name)
        connector.signed_in = True
        self._save(connector)
        log.info("Signed in to %s", connector.title)
        return connector

    def _signed_out(self, connector: Connector) -> None:
        connector = self._get(connector.name)
        connector.signed_in = False
        self._save(connector)

    def _get(self, name: str) -> Connector:
        if not (data := self._store.connectors().get(name)):
            raise KeyError(f"No connector called {name!r}")
        return _connector(data)

    def _save(self, connector: Connector) -> None:
        self._store.save_connector(connector.name, connector.__dict__)


@dataclass
class _SignIn:
    name: str
    code: asyncio.Future | None = None
    connecting: asyncio.Task | None = None
    started: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started > SIGN_IN_SECONDS


class _Storage:
    """The MCP SDK's tokens and registration, kept with the connector."""

    def __init__(self, connectors: Connectors, name: str):
        self._connectors, self._name = connectors, name

    async def get_tokens(self) -> OAuthToken | None:
        tokens = self._connectors._get(self._name).tokens
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        connector = self._connectors._get(self._name)
        connector.tokens = tokens.model_dump(mode="json", exclude_none=True)
        self._connectors._save(connector)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        connector = self._connectors._get(self._name)
        if connector.client:
            return OAuthClientInformationFull.model_validate(connector.client)
        if "client_id" in connector.settings:  # the owner's own app (Slack): nothing to register
            return OAuthClientInformationFull(
                client_id=connector.settings["client_id"],
                client_secret=connector.settings.get("client_secret"),
                redirect_uris=[self._connectors._callback],
                token_endpoint_auth_method="client_secret_post",
            )
        return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        connector = self._connectors._get(self._name)
        connector.client = client_info.model_dump(mode="json", exclude_none=True)
        self._connectors._save(connector)


class _Guarded(WrapperToolset):
    """A connector's tools as the helper gets them: named after the connector, the read-only ones free, the rest
    paused for the owner's approval. A connector that won't connect drops out, marked signed out."""

    def __init__(self, wrapped, connector: Connector, mark: Callable[[str], None], signed_out):
        super().__init__(wrapped)
        self._connector, self._mark, self._signed_out = connector, mark, signed_out
        self._failed = False

    @property
    def id(self) -> str:
        return self._connector.name

    async def __aenter__(self):
        try:
            await self.wrapped.__aenter__()
        except Exception:
            log.exception("Couldn't connect to %s", self._connector.title)
            self._failed = True
            self._signed_out(self._connector)
            self._mark(f"Couldn't reach {self._connector.title}: sign in again from Connectors.")
        return self

    async def __aexit__(self, *args):
        if not self._failed:
            return await self.wrapped.__aexit__(*args)
        return None

    async def get_tools(self, ctx: RunContext) -> dict[str, ToolsetTool]:
        return {} if self._failed else await super().get_tools(ctx)

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: RunContext, tool: ToolsetTool) -> Any:
        action = f"{self._connector.title}: {name} {json.dumps(tool_args, ensure_ascii=False)}"
        annotations = (tool.tool_def.metadata or {}).get("annotations") or {}
        if not annotations.get("readOnlyHint") and not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"action": action})
        self._mark(action)
        return await super().call_tool(name, tool_args, ctx, tool)


def _connector(data: dict) -> Connector:
    return Connector(**data)


def _state(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def _set(future: asyncio.Future, value: Any) -> None:
    if not future.done():
        future.set_result(value)
