import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic_ai import ModelResponse, TextPart, Tool, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from agent.connectors import GOOGLE_TOKEN, GOOGLE_USER, Connector, Connectors, _Guarded
from agent.helper import Helper
from agent.runs import Answer

SITE = "https://archangel.test"


def google(request: httpx.Request) -> httpx.Response:
    if str(request.url) == GOOGLE_TOKEN:
        assert parse_qs(request.content.decode())["code"] == ["the-code"]
        return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3599})
    assert str(request.url) == GOOGLE_USER and request.headers["Authorization"] == "Bearer at"
    return httpx.Response(200, json={"email": "owner@gmail.com"})


def connectors(store, tmp_path) -> Connectors:
    return Connectors(store, SITE, tmp_path, httpx.AsyncClient(transport=httpx.MockTransport(google)))


def test_services_need_what_they_need(store, tmp_path):
    with pytest.raises(ValueError, match="client_secret"):
        connectors(store, tmp_path).add("slack", {"client_id": "1"})


def test_other_mcp_servers_are_named_after_their_host(store, tmp_path):
    connector = connectors(store, tmp_path).add("mcp", {"url": "https://mcp.linear.app/mcp"})
    assert (connector.name, connector.title, connector.url) == ("mcp_linear_app", "mcp.linear.app", connector.url)


def test_signing_in_to_google_keeps_a_refresh_token_for_its_connector(store, tmp_path):
    async def scenario():
        found = connectors(store, tmp_path)
        found.add("google", {"client_id": "id", "client_secret": "secret"})
        link = urlparse(await found.sign_in("google"))
        query = parse_qs(link.query)
        assert query["redirect_uri"] == [f"{SITE}/connectors/callback"] and query["access_type"] == ["offline"]

        connector = await found.finish(query["state"][0], "the-code")
        assert (connector.account, connector.signed_in) == ("owner@gmail.com", True)

        found._workspace(connector)  # what workspace-mcp starts from
        credentials = json.loads((tmp_path / "google" / "owner@gmail.com.json").read_text())
        assert (credentials["refresh_token"], credentials["client_id"]) == ("rt", "id")

    asyncio.run(scenario())


def test_a_sign_in_only_finishes_once(store, tmp_path):
    async def scenario():
        found = connectors(store, tmp_path)
        found.add("google", {"client_id": "id", "client_secret": "secret"})
        state = parse_qs(urlparse(await found.sign_in("google")).query)["state"][0]
        await found.finish(state, "the-code")
        with pytest.raises(ValueError, match="expired"):
            await found.finish(state, "the-code")

    asyncio.run(scenario())


class Notion:
    """Connectors with one signed-in service whose tools are plain functions, labelled as MCP servers label them."""

    def __init__(self, failing: bool = False):
        self.done: list[str] = []
        self.connector = Connector("notion", "notion", "Notion", signed_in=True)
        self.failing = failing
        self.signed_out: list[str] = []

        def search(query: str) -> str:
            return f"Found {query}."

        def create_page(title: str) -> str:
            self.done.append(title)
            return "Created."

        self.tools = FunctionToolset(
            [Tool(search, metadata={"annotations": {"readOnlyHint": True}}), Tool(create_page)]
        )

    def connected(self) -> list[Connector]:
        return [self.connector]

    def toolsets(self, mark):
        tools = Broken() if self.failing else self.tools
        return [_Guarded(tools, self.connector, mark, lambda c: self.signed_out.append(c.name)).prefixed("notion")]


class Broken(FunctionToolset):
    async def __aenter__(self):
        raise RuntimeError("401")


def scripted(*calls: tuple[str, dict]) -> FunctionModel:
    """Makes each call in turn, then answers with the last result."""

    def model(messages, info) -> ModelResponse:
        made = sum(isinstance(p, ToolCallPart) for m in messages for p in m.parts)
        if made < len(calls):
            return ModelResponse(parts=[ToolCallPart(*calls[made])])
        return ModelResponse(parts=[TextPart(str(messages[-1].parts[-1].content))])

    return FunctionModel(model)


def run(helper: Helper, steps: list[str], **resume):
    return asyncio.run(helper.run("Put my notes in Notion", lambda text, screen: steps.append(text), **resume))


def test_read_only_tools_run_freely():
    steps = []
    outcome = run(Helper(scripted(("notion_search", {"query": "notes"})), Notion()), steps)
    assert (outcome.status, outcome.report) == ("done", "Found notes.")
    assert steps == ["Started.", 'Notion: search {"query": "notes"}']


def test_anything_else_waits_for_the_owner_and_runs_once_approved():
    notion = Notion()
    helper = Helper(scripted(("notion_create_page", {"title": "Ideas"})), notion)

    waiting = run(helper, [])
    assert (waiting.status, waiting.report) == ("needs_approval", 'Approve: Notion: create_page {"title": "Ideas"}?')
    assert notion.done == []

    done = run(helper, [], paused=waiting.run, answer=Answer("yes", approved=True))
    assert (done.status, notion.done) == ("done", ["Ideas"])


def test_a_service_that_wont_connect_drops_out_and_needs_signing_in_again():
    notion, steps = Notion(failing=True), []
    outcome = run(Helper(scripted(), notion), steps)
    assert outcome.status == "done"
    assert notion.signed_out == ["notion"]
    assert "Couldn't reach Notion: sign in again from Connectors." in steps


def test_nothing_connected_is_a_failure_to_pass_on():
    class Nothing(Notion):
        def connected(self):
            return []

    assert run(Helper(scripted(), Nothing()), []).status == "failed"
