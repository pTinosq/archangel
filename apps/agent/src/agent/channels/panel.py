"""The control panel's side of the agent: a channel for chatting, plus an HTTP API (on `Web`) onto the transcript,
tasks and their steps, and the prompts. The React app in apps/panel polls it, and watches live activity as a
stream (server-sent events: one way, so nothing can be commanded through it).

Its buttons (approve, decline, cancel) send ordinary owner messages, so every decision goes through the one
conversation and the orchestrator knows about it.
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import activity
from agent.channels import Channel, Deliver
from agent.messages import Address, Message, Text
from agent.prompts import DEFAULTS, Prompt, Prompts
from agent.store import Line, Step, Store, Task
from agent.web import Web

log = logging.getLogger(__name__)

FOREVER = "max-age=31536000, immutable"  # an image never changes under its name
HEARTBEAT_SECONDS = 1


class PanelChannel(Channel):
    name = "panel"
    owner = Address(name, "owner")

    def __init__(self, store: Store, prompts: Prompts, web: Web):
        self._deliver: Deliver | None = None
        self._web = web
        _routes(web.app, self, store, prompts)

    async def start(self, deliver: Deliver) -> None:
        self._deliver = deliver
        await self._web.closed()

    async def send(self, message: Message) -> None:
        pass  # the panel shows the transcript, where the gateway has already put this

    @property
    def stopping(self) -> bool:
        return self._web.stopping

    async def receive(self, text: str) -> None:
        if self._deliver:
            await self._deliver(Message(self.owner, (Text(text),)))


class Said(BaseModel):
    text: str


def _routes(app: FastAPI, channel: PanelChannel, store: Store, prompts: Prompts) -> None:
    # Every route is async: sync ones run on a thread pool, and the store's SQLite connection belongs to this thread.

    @app.get("/api/transcript")
    async def transcript(after: int = 0) -> list[Line]:
        return store.lines(after)

    @app.post("/api/messages")
    async def say(said: Said) -> None:
        await channel.receive(said.text)

    @app.get("/api/tasks")
    async def tasks() -> list[dict]:
        return [_task(task) for task in store.tasks()]

    @app.get("/api/tasks/{id}/steps")
    async def steps(id: int, after: int = 0) -> list[Step]:
        return store.steps(id, after)

    @app.get("/api/images/{name}")
    async def image(name: str) -> Response:
        if not (found := store.image(name)):
            raise HTTPException(404, "No such image, or it has expired")
        return Response(found.data, media_type=found.media_type, headers={"Cache-Control": FOREVER})

    @app.get("/api/activity")
    async def live() -> StreamingResponse:
        return StreamingResponse(
            _events(channel), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/api/prompts")
    async def list_prompts() -> list[dict]:
        return [_prompt(prompt) for prompt in prompts.all()]

    @app.put("/api/prompts/{name}")
    async def edit_prompt(name: str, said: Said) -> dict:
        return _prompt(_edit(prompts, name, said.text))

    @app.delete("/api/prompts/{name}")
    async def reset_prompt(name: str) -> dict:
        return _prompt(_edit(prompts, name, None))


async def _events(channel: PanelChannel) -> AsyncIterator[str]:
    # Ends itself on shutdown: the server waits for open responses, and this one would never finish.
    async with aclosing(activity.listen(idle=HEARTBEAT_SECONDS)) as notices:
        async for notice in notices:
            if channel.stopping:
                return
            yield f"data: {json.dumps(asdict(notice))}\n\n" if notice else ": still here\n\n"


def _task(task: Task) -> dict:
    return {
        "id": task.id,
        "goal": task.goal,
        "status": task.status,
        "open": task.status.open,
        "report": task.report,
        "reportTo": task.report_to.channel,
    }


def _prompt(prompt: Prompt) -> dict:
    return {"name": prompt.name, "text": prompt.text, "default": prompt.default, "edited": prompt.edited}


def _edit(prompts: Prompts, name: str, text: str | None) -> Prompt:
    if name not in DEFAULTS:
        raise HTTPException(404, f"No prompt called {name!r}")
    prompts.edit(name, text)
    return prompts.get(name)
