import asyncio
import json
import tempfile
from pathlib import Path

import httpx
from fakes import FakeHelper, FakePilot, FakeReporter, FakeResearcher

from agent import activity, web
from agent.channels import panel
from agent.channels.login import GitHub
from agent.channels.panel import PanelChannel
from agent.connectors import Connectors
from agent.messages import Address, Message, Text
from agent.prompts import Prompts
from agent.store import Store
from agent.tasks import Tasks
from agent.web import Web


class Panel:
    def __init__(self, store: Store, github: GitHub | None = None):
        self.store = store
        self.prompts = Prompts(store)
        self.web = Web(github)
        self.connectors = Connectors(store, "http://localhost", Path(tempfile.mkdtemp()))
        self.channel = PanelChannel(store, self.prompts, self.connectors, self.web)
        self.delivered: list[Message] = []

    def call(self, method: str, url: str, host: str = "localhost", **kwargs) -> httpx.Response:
        async def go():
            async def deliver(message):
                self.delivered.append(message)

            self.channel._deliver = deliver
            transport = httpx.ASGITransport(self.web.finish())
            async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(go())


def test_messages_go_to_the_conversation(store):
    panel = Panel(store)
    assert panel.call("POST", "/api/messages", json={"text": "hi"}).status_code == 200
    assert panel.delivered == [Message(PanelChannel.owner, (Text("hi"),))]


def test_transcript_and_its_images(store):
    store.add_line(Message(Address("cli", "local"), (Text("hi"),)), "owner")
    panel = Panel(store)

    [line] = panel.call("GET", "/api/transcript").json()
    assert (line["channel"], line["sender"], line["text"]) == ("cli", "owner", "hi")
    assert panel.call("GET", f"/api/transcript?after={line['id']}").json() == []


def test_tasks_steps_and_screens(store):
    async def start():
        tasks = Tasks(store, FakePilot(), FakeResearcher(), FakeReporter(), FakeHelper())
        tasks.start("post hello", Address("cli", "local"))
        await tasks.idle()

    asyncio.run(start())
    panel = Panel(store)

    [task] = panel.call("GET", "/api/tasks").json()
    assert (task["goal"], task["status"], task["open"], task["reportTo"]) == (
        "post hello",
        "needs approval",
        True,
        "cli",
    )
    first, waiting = panel.call("GET", "/api/tasks/1/steps").json()
    screen = panel.call("GET", f"/api/images/{first['screen']}")
    assert (screen.content, screen.headers["content-type"]) == (b"jpg", "image/jpeg")
    assert waiting["screen"] is None
    assert panel.call("GET", "/api/images/nope.jpg").status_code == 404


def test_prompts_edit_and_reset(store):
    panel = Panel(store)
    edited = panel.call("PUT", "/api/prompts/guard", json={"text": "Always ask."}).json()
    assert (edited["text"], edited["edited"]) == ("Always ask.", True)
    assert panel.prompts.live("guard")() == "Always ask."
    assert Prompts(store)["guard"] == "Always ask."  # survives a restart

    reset = panel.call("DELETE", "/api/prompts/guard").json()
    assert reset["edited"] is False
    assert panel.prompts["guard"] == reset["default"]
    assert panel.call("PUT", "/api/prompts/nope", json={"text": "x"}).status_code == 404


def test_refuses_other_hosts(store):
    # A page on evil.example resolving to 127.0.0.1 still sends its own Host.
    assert Panel(store).call("GET", "/api/tasks", host="evil.example").status_code == 400


def test_public_only_behind_sign_in(store):
    public = Panel(store, GitHub("id", "secret", "owner"))
    assert public.call("GET", "/api/tasks", host="archangel.up.railway.app").status_code == 401
    assert public.call("GET", "/health", host="archangel.up.railway.app").status_code == 200


def test_serves_the_built_app(store, tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<h1>panel</h1>")
    monkeypatch.setattr(web, "DIST", tmp_path)
    assert Panel(store).call("GET", "/").text == "<h1>panel</h1>"
    assert Panel(store).call("GET", "/api/tasks").json() == []


def test_activity_streams_as_server_sent_events():
    activity.hop("gateway", "message", "hi", source="cli")

    async def first():
        events = panel._events(PanelChannel(None, None, None, Web()))
        line = await anext(events)
        await events.aclose()
        return line

    line = asyncio.run(first())
    assert line.startswith("data: ") and line.endswith("\n\n")
    notice = json.loads(line.removeprefix("data: "))
    assert (notice["source"], notice["target"], notice["text"]) == ("cli", "gateway", "hi")


def test_research_reports_are_pages_that_run_nothing(store):
    async def start():
        tasks = Tasks(store, FakePilot(), FakeResearcher(), FakeReporter(), FakeHelper())
        tasks.start("the best funds", Address("cli", "local"), "research")
        await tasks.idle()

    asyncio.run(start())
    panel = Panel(store)

    [task] = panel.call("GET", "/api/tasks").json()
    assert (task["kind"], task["page"]) == ("research", True)
    page = panel.call("GET", "/reports/1")
    assert "<p>All about the best funds.</p>" in page.text
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    assert panel.call("GET", "/reports/2").status_code == 404


def test_connectors_are_added_and_listed_without_their_secrets(store):
    panel = Panel(store)
    added = panel.call("POST", "/api/connectors", json={"kind": "google", "settings": {"client_id": "id"}})
    assert added.status_code == 400  # no client secret

    assert "http://localhost/connectors/callback" in panel.call("GET", "/api/connectors").json()["services"][0]["help"]
    settings = {"client_id": "id", "client_secret": "secret"}
    added = panel.call("POST", "/api/connectors", json={"kind": "google", "settings": settings, "profile": "work"})
    assert added.json()["signIn"].startswith("https://accounts.google.com/")

    listed = panel.call("GET", "/api/connectors").json()
    assert listed["connectors"] == [
        {
            "name": "work_google",
            "kind": "google",
            "title": "Gmail and Calendar",
            "account": "",
            "profile": "work",
            "signedIn": False,
        }
    ]
    assert "secret" not in json.dumps(listed["connectors"])

    assert "Not connected" in panel.call("GET", "/connectors/callback?state=unknown&code=x").text
    panel.call("DELETE", "/api/connectors/work_google")
    assert panel.call("GET", "/api/connectors").json()["connectors"] == []
