"""The control panel's side of the agent: a chat channel, plus a local HTTP API onto tasks, their steps and
the prompts. The React app in apps/panel polls it.

Local only. It can approve payments, so it binds to 127.0.0.1 and refuses any other Host header: a web page
can't reach it by pointing its own domain at this machine (DNS rebinding).
"""

import logging

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from agent.channels import Channel, Deliver
from agent.messages import Conversation, Image, Message, Text
from agent.phone.pilot import Answer
from agent.prompts import DEFAULTS, Prompt, Prompts
from agent.store import Line, Step, Store, Task
from agent.tasks import Tasks

log = logging.getLogger(__name__)

PORT = 8765
HOSTS = ["localhost", "127.0.0.1"]
TASKS_SHOWN = 100
FOREVER = "max-age=31536000, immutable"  # screenshots never change once taken


class PanelChannel(Channel):
    name = "panel"
    conversation = Conversation(name, "owner")

    def __init__(self, store: Store, tasks: Tasks, prompts: Prompts):
        self._store = store
        self._deliver: Deliver | None = None
        self.app = _api(self, store, tasks, prompts)

    async def start(self, deliver: Deliver) -> None:
        self._deliver = deliver
        config = uvicorn.Config(self.app, host="127.0.0.1", port=PORT, log_level="warning")
        log.info("Control panel API on http://127.0.0.1:%d; `just panel` serves the app", PORT)
        await uvicorn.Server(config).serve()

    async def send(self, message: Message) -> None:
        image = next((part for part in message.parts if isinstance(part, Image)), None)
        self._store.add_line(
            message.conversation, "archangel", message.text, image and image.data, image and image.media_type
        )

    async def receive(self, text: str) -> None:
        self._store.add_line(self.conversation, "owner", text)
        if self._deliver:
            await self._deliver(Message(self.conversation, (Text(text),)))


class Said(BaseModel):
    text: str


class Reply(BaseModel):
    text: str = ""
    approve: bool = False


def _api(channel: PanelChannel, store: Store, tasks: Tasks, prompts: Prompts) -> FastAPI:
    # Every route is async: sync ones run on a thread pool, and the store's SQLite connection belongs to this thread.
    app = FastAPI(title="Archangel control panel")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=HOSTS)

    @app.get("/api/tasks")
    async def list_tasks() -> list[dict]:
        return [_task(task) for task in store.recent_tasks(TASKS_SHOWN)]

    @app.get("/api/tasks/{id}/steps")
    async def steps(id: int, after: int = 0) -> list[Step]:
        return store.steps(id, after)

    @app.post("/api/tasks/{id}/answer")
    async def answer(id: int, reply: Reply) -> dict:
        return {"result": tasks.answer(id, Answer(reply.text, reply.approve))}

    @app.post("/api/tasks/{id}/cancel")
    async def cancel(id: int) -> dict:
        return {"result": tasks.cancel(id)}

    @app.get("/api/steps/{id}/screen")
    async def screen(id: int) -> Response:
        if not (jpeg := store.screen(id)):
            raise HTTPException(404)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": FOREVER})

    @app.get("/api/chat")
    async def chat(after: int = 0) -> list[Line]:
        return store.lines(channel.conversation, after)

    @app.post("/api/chat")
    async def say(said: Said) -> None:
        await channel.receive(said.text)

    @app.get("/api/chat/{id}/image")
    async def chat_image(id: int) -> Response:
        if not (image := store.line_image(id)):
            raise HTTPException(404)
        return Response(image[0], media_type=image[1], headers={"Cache-Control": FOREVER})

    @app.get("/api/prompts")
    async def list_prompts() -> list[dict]:
        return [_prompt(prompt) for prompt in prompts.all()]

    @app.put("/api/prompts/{name}")
    async def edit_prompt(name: str, said: Said) -> dict:
        _known(name)
        prompts.edit(name, said.text)
        return _prompt(next(p for p in prompts.all() if p.name == name))

    @app.delete("/api/prompts/{name}")
    async def reset_prompt(name: str) -> dict:
        _known(name)
        prompts.edit(name, None)
        return _prompt(next(p for p in prompts.all() if p.name == name))

    return app


def _task(task: Task) -> dict:
    return {
        "id": task.id,
        "conversation": f"{task.conversation.channel}/{task.conversation.id}",
        "goal": task.goal,
        "status": task.status,
        "open": task.status.open,
        "report": task.report,
    }


def _prompt(prompt: Prompt) -> dict:
    return {"name": prompt.name, "text": prompt.text, "default": prompt.default, "edited": prompt.edited}


def _known(name: str) -> None:
    if name not in DEFAULTS:
        raise HTTPException(404, f"No prompt called {name!r}")
