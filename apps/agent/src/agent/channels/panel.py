"""The control panel's side of the agent: a channel for chatting, plus a local HTTP API onto the transcript,
tasks and their steps, and the prompts. The React app in apps/panel polls it, and watches live activity as a
stream (server-sent events: one way, so nothing can be commanded through it).

Its buttons (approve, decline, cancel) send ordinary owner messages, so every decision goes through the one
conversation and the orchestrator knows about it.

It speaks for the owner. Locally it binds to 127.0.0.1 and refuses any other Host header: a web page can't reach
it by pointing its own domain at this machine (DNS rebinding). It only listens publicly with GitHub sign-in, and
then serves the built app (apps/panel/dist) too, so the browser sees one origin.
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import activity
from agent.channels import Channel, Deliver
from agent.channels.login import GitHub, protect
from agent.messages import Address, Message, Text
from agent.prompts import DEFAULTS, Prompt, Prompts
from agent.store import Line, Step, Store, Task

log = logging.getLogger(__name__)

PORT = 8765
DIST = Path("../panel/dist")  # from apps/agent
HOSTS = ["localhost", "127.0.0.1"]
FOREVER = "max-age=31536000, immutable"  # an image never changes under its name
HEARTBEAT_SECONDS = 1


class PanelChannel(Channel):
    name = "panel"
    owner = Address(name, "owner")

    def __init__(self, store: Store, prompts: Prompts, github: GitHub | None = None, port: int = PORT):
        """With `github`, the panel is public behind sign-in; without, it's local only."""
        self._deliver: Deliver | None = None
        self._server: uvicorn.Server | None = None
        self._host, self._port = ("0.0.0.0" if github else "127.0.0.1"), port
        self.app = _api(self, store, prompts, github)

    async def start(self, deliver: Deliver) -> None:
        self._deliver = deliver
        config = uvicorn.Config(self.app, host=self._host, port=self._port, log_level="warning")
        log.info("Control panel on http://%s:%d", self._host, self._port)
        self._server = uvicorn.Server(config)
        try:
            await self._server.serve()
        except SystemExit:  # uvicorn's way of saying it couldn't start, usually the port
            raise RuntimeError(
                f"Control panel couldn't start on port {self._port}. Is Archangel already running?"
            ) from None

    async def send(self, message: Message) -> None:
        pass  # the panel shows the transcript, where the gateway has already put this

    @property
    def stopping(self) -> bool:
        return bool(self._server and self._server.should_exit)

    async def receive(self, text: str) -> None:
        if self._deliver:
            await self._deliver(Message(self.owner, (Text(text),)))


class Said(BaseModel):
    text: str


def _api(channel: PanelChannel, store: Store, prompts: Prompts, github: GitHub | None) -> FastAPI:
    # Every route is async: sync ones run on a thread pool, and the store's SQLite connection belongs to this thread.
    app = FastAPI(title="Archangel control panel")
    if github:
        protect(app, github)
    else:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=HOSTS)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

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

    if DIST.is_dir():  # built for deploying; locally, `just panel` serves the app
        app.mount("/", StaticFiles(directory=DIST, html=True))
    return app


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
