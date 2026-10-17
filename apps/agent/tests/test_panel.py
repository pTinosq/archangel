import asyncio

import httpx
import pydantic_ai

from agent.channels.panel import PanelChannel
from agent.messages import Conversation, Image, Message, Text
from agent.phone.pilot import Outcome
from agent.phone.relay import Screenshot
from agent.prompts import Prompts
from agent.store import Status, Store
from agent.tasks import Tasks

CLI = Conversation("cli", "local")


class FakePilot:
    """Records a step with a screenshot, then asks for approval; approved, it's done."""

    async def run(self, goal, paused=None, answer=None, record=None) -> Outcome:
        record("Tapped the Post button.", Screenshot(b"jpeg", 540, 1200))
        if paused:
            return Outcome("done" if answer.approved else "gave_up", "Posted." if answer.approved else "Not posted.")
        return Outcome("waiting", "Approve: post it?", run=pydantic_ai.Conversation())


class Panel:
    def __init__(self):
        self.store = Store(":memory:")
        self.tasks = Tasks(self.store, FakePilot())
        self.prompts = Prompts(self.store)
        self.channel = PanelChannel(self.store, self.tasks, self.prompts)
        self.delivered: list[Message] = []

    def call(self, method: str, url: str, host: str = "localhost", **kwargs) -> httpx.Response:
        async def go():
            async def deliver(message):
                self.delivered.append(message)

            self.channel._deliver = deliver
            transport = httpx.ASGITransport(self.channel.app)
            async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as client:
                response = await client.request(method, url, **kwargs)
            await self.tasks.idle()
            return response

        return asyncio.run(go())


def test_chat_round_trip():
    panel = Panel()
    assert panel.call("POST", "/api/chat", json={"text": "hi"}).status_code == 200
    assert panel.delivered == [Message(PanelChannel.conversation, (Text("hi"),))]

    reply = Message(PanelChannel.conversation, (Text("Post this?"), Image(b"png", "image/png")))
    asyncio.run(panel.channel.send(reply))

    lines = panel.call("GET", "/api/chat").json()
    assert [(line["sender"], line["text"], line["image"]) for line in lines] == [
        ("owner", "hi", None),
        ("archangel", "Post this?", "image/png"),
    ]
    image = panel.call("GET", f"/api/chat/{lines[1]['id']}/image")
    assert (image.content, image.headers["content-type"]) == (b"png", "image/png")
    assert panel.call("GET", f"/api/chat?after={lines[0]['id']}").json() == lines[1:]


def test_tasks_steps_and_screens():
    panel = Panel()

    async def start():
        panel.tasks.start(CLI, "post hello")
        await panel.tasks.idle()

    asyncio.run(start())

    [task] = panel.call("GET", "/api/tasks").json()
    assert (task["goal"], task["status"], task["open"], task["conversation"]) == (
        "post hello",
        "waiting",
        True,
        "cli/local",
    )
    steps = panel.call("GET", "/api/tasks/1/steps").json()
    assert [(s["text"], s["screen"]) for s in steps] == [
        ("Tapped the Post button.", True),
        ("waiting: Approve: post it?", False),
    ]
    assert panel.call("GET", f"/api/steps/{steps[0]['id']}/screen").content == b"jpeg"
    assert panel.call("GET", f"/api/steps/{steps[1]['id']}/screen").status_code == 404


def test_approving_from_the_panel():
    panel = Panel()

    async def start():
        panel.tasks.start(CLI, "post hello")
        await panel.tasks.idle()

    asyncio.run(start())

    assert panel.call("POST", "/api/tasks/1/answer", json={"approve": True}).json() == {"result": "Task #1 continues."}
    assert (panel.store.task(1).status, panel.store.task(1).report) == (Status.DONE, "Posted.")


def test_prompts_edit_and_reset():
    panel = Panel()
    edited = panel.call("PUT", "/api/prompts/guard", json={"text": "Always ask."}).json()
    assert (edited["text"], edited["edited"]) == ("Always ask.", True)
    assert panel.prompts.live("guard")() == "Always ask."
    assert Prompts(panel.store)["guard"] == "Always ask."  # survives a restart

    reset = panel.call("DELETE", "/api/prompts/guard").json()
    assert reset["edited"] is False
    assert panel.prompts["guard"] == reset["default"]
    assert panel.call("PUT", "/api/prompts/nope", json={"text": "x"}).status_code == 404


def test_refuses_other_hosts():
    # A page on evil.example resolving to 127.0.0.1 still sends its own Host.
    assert Panel().call("GET", "/api/tasks", host="evil.example").status_code == 400
