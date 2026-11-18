import asyncio
import contextlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic_ai import ModelResponse, TextPart, Tool, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from agent import activity, gmail
from agent.connectors import GOOGLE_TOKEN, GOOGLE_USER, SIGN_IN_SECONDS, Connector, Connectors, _Guarded
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

        assert found._workspace(connector).keep_alive is False  # ends with its run, not left holding memory
        credentials = json.loads((tmp_path / "google" / "google" / "owner@gmail.com.json").read_text())
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


def test_workspace_mcp_keeps_the_token_it_refreshed_until_the_owner_signs_in_again(store, tmp_path):
    async def scenario():
        found = connectors(store, tmp_path)
        found.add("google", {"client_id": "id", "client_secret": "secret"})
        state = parse_qs(urlparse(await found.sign_in("google")).query)["state"][0]
        connector = await found.finish(state, "the-code")
        path = tmp_path / "google" / "google" / "owner@gmail.com.json"

        found._workspace(connector)
        first = json.loads(path.read_text())
        assert first["token"] == "at" and datetime.fromisoformat(first["expiry"]) > datetime.now(UTC).replace(
            tzinfo=None
        )  # with an expiry, a stale token is refreshed before use, not sent to meet a 401

        refreshed = {**first, "token": "at-2", "expiry": "2026-11-18T12:00:00"}  # as workspace-mcp saves it
        path.write_text(json.dumps(refreshed))
        found._workspace(connector)
        assert json.loads(path.read_text()) == refreshed

        connector.tokens = {"access_token": "new", "refresh_token": "rt-2"}  # signed in again, long ago
        found._workspace(connector)
        again = json.loads(path.read_text())
        assert (again["token"], again["refresh_token"], again["expiry"]) == ("new", "rt-2", "1970-01-01T00:00:00")

    asyncio.run(scenario())


def test_a_gmail_message_comes_with_its_links_and_without_inline_images(tmp_path):
    files = tmp_path / "files"
    files.mkdir()
    image = "data:image/png;base64," + "iVBORw0KGgo" * 3000
    button = '<a href="https://shop.example.com/track?order=123&amp;ref=email"><b>Track your parcel</b></a>'
    html = f'<p>Hi Sam,</p><img src="{image}"><p>Your order is on its way.</p>{button}'
    html += '<a href="https://shop.example.com/track?order=123&ref=email">here</a>'  # the same link again
    html += '<a href="https://shop.example.com"><img src="https://shop.example.com/logo.png" alt="Example Shop"></a>'
    calls = []

    async def call_tool(name, args):
        calls.append(args)
        if args.get("full"):
            saved = files / "Your order.html"
            saved.write_text(html)
            return f"Subject: Your order\n\n--- FULL MESSAGE EXPORT ---\n📎 Saved to: {saved}\n"
        return f"Subject: Your order\n\n--- BODY ---\n{html[:20000]}\n\n[Content truncated...]"  # cut before the button

    process = gmail.processor(files)
    result = asyncio.run(process(None, call_tool, "get_gmail_message_content", {"message_id": "m1"}))

    assert "iVBORw0KGgo" not in result and "data:[inline image]" in result
    assert result.endswith(
        "--- LINKS (from the HTML) ---\n"
        "Track your parcel → https://shop.example.com/track?order=123&ref=email\n"
        "Example Shop → https://shop.example.com"
    )
    assert calls[1] == {"message_id": "m1", "full": True, "body_format": "html"}
    assert not list(files.iterdir())  # read and gone

    other = asyncio.run(process(None, call_tool, "search_gmail_messages", {"query": "order"}))
    assert "iVBORw0KGgo" in other  # only message contents are touched


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

    def describe(self, prefixes: bool = False) -> str:
        return "Notion"

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
    calls = [(n.source, n.target, n.dir, n.target_role) for n in activity._recent if n.kind == "service"]
    assert calls == [("helper", "notion", "tx", "service"), ("notion", "helper", "rx", "agent")]  # for the cockpit


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


def test_several_accounts_of_a_service_live_side_by_side_in_profiles(store, tmp_path):
    async def scenario():
        found = connectors(store, tmp_path)
        personal = found.add("google", {"client_id": "id", "client_secret": "secret"}, "Personal")
        with pytest.raises(ValueError, match="client_id"):  # each account its own Google client
            found.add("google", {}, "work")
        work = found.add("google", {"client_id": "work-id", "client_secret": "work-secret"}, "work")
        assert (personal.name, work.name, work.settings["client_id"]) == ("personal_google", "work_google", "work-id")
        assert found.add("notion", {}).name == "notion"
        assert found.add("notion", {}).name == "notion_2"

        for connector in (personal, work):
            state = parse_qs(urlparse(await found.sign_in(connector.name)).query)["state"][0]
            await found.finish(state, "the-code")
        assert found.describe() == "personal: Gmail and Calendar (owner@gmail.com); work: Gmail and Calendar (owner@gmail.com)"
        assert "[tools work_google_…]" in found.describe(prefixes=True)

    asyncio.run(scenario())


def test_the_helper_reads_whats_connected_from_the_store_itself(store, tmp_path):
    # SQLite belongs to the thread that opened it: nothing the helper runs may read the store on another.
    class Stored(Connectors):
        def toolsets(self, mark):
            return [Notion().toolsets(mark)[0]]

    connectors = Stored(store, SITE, tmp_path)
    connectors._save(Connector("personal_google", "google", "Gmail and Calendar", signed_in=True, profile="personal"))
    seen = []

    def model(messages, info) -> ModelResponse:
        seen.append(info.instructions)
        return ModelResponse(parts=[TextPart("Nothing new.")])

    async def scenario():  # the store, like the agent's, opened on the thread that runs the helper
        return await Helper(FunctionModel(model), connectors).run("Latest email", lambda text, screen: None)

    assert asyncio.run(scenario()).status == "done"
    assert "personal: Gmail and Calendar [tools personal_google_…]" in seen[0]


def test_signing_in_again_registers_afresh(store, tmp_path):
    # An old registration may be revoked, or send the owner back to an address Archangel has left.
    found = connectors(store, tmp_path)
    notion = found.add("notion", {})
    notion.client, notion.tokens = {"client_id": "old", "redirect_uris": ["https://old.test/connectors/callback"]}, {"access_token": "old"}
    found._save(notion)

    async def connect(connector, auth):
        assert await auth.context.storage.get_client_info() is None
        assert await auth.context.storage.get_tokens() is None

    found._connect = connect
    assert asyncio.run(found.sign_in("notion")) is None


def test_a_hosted_sign_in_hands_the_sdk_the_code_and_its_issuer(store, tmp_path):
    # Notion advertises RFC 9207: the SDK refuses a code that comes back without its issuer.
    found, came_back = connectors(store, tmp_path), []
    found.add("notion", {})

    async def connect(connector, auth):
        await auth.context.redirect_handler("https://mcp.notion.com/authorize?state=s1")
        came_back.append(await auth.context.callback_handler())

    found._connect = connect

    async def scenario():
        assert await found.sign_in("notion") == "https://mcp.notion.com/authorize?state=s1"
        return await found.finish("s1", "the-code", "https://mcp.notion.com")

    assert asyncio.run(scenario()).signed_in
    assert (came_back[0].code, came_back[0].state, came_back[0].iss) == ("the-code", "s1", "https://mcp.notion.com")


def test_signing_in_waits_as_long_as_the_owner_may_take(store, tmp_path, monkeypatch):
    # MCPToolset's own wait is 5 seconds, and the owner signs in on Notion's page within it.
    opened = []

    class Toolset:
        def __init__(self, url, **options):
            opened.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("agent.connectors.MCPToolset", Toolset)
    found = connectors(store, tmp_path)
    asyncio.run(found._connect(found.add("notion", {}), None))
    assert opened[0]["init_timeout"] == SIGN_IN_SECONDS


def test_a_sign_in_that_gave_up_says_why_when_the_owner_comes_back(store, tmp_path):
    found = connectors(store, tmp_path)
    found.add("notion", {})

    async def connect(connector, auth):
        # As MCPToolset's own timeout does: the wait for the owner's code is cancelled, then it fails.
        await auth.context.redirect_handler("https://mcp.notion.com/authorize?state=s1")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(auth.context.callback_handler(), 0.001)
        raise RuntimeError("Failed to initialize server session")

    found._connect = connect

    async def scenario():
        await found.sign_in("notion")
        await asyncio.sleep(0.01)
        return await found.finish("s1", "the-code", "https://mcp.notion.com")

    with pytest.raises(RuntimeError, match="Failed to initialize server session"):
        asyncio.run(scenario())
