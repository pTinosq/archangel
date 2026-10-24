"""The control panel's side of the agent: a channel for chatting, plus an HTTP API (on `Web`) onto the transcript,
tasks and their steps, and the prompts. The React app in apps/panel polls it, and watches live activity as a
stream (server-sent events: one way, so nothing can be commanded through it).

The Twin page shows the context twin's graph, and lets the owner edit or delete a node (`Graph`).

The Connectors page adds the owner's online services and signs in to them (`Connectors`); services send the owner
back to /connectors/callback.

Its buttons (approve, decline, cancel) send ordinary owner messages, so every decision goes through the one
conversation and the orchestrator knows about it.
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import asdict
from html import escape

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from agent import activity, evals_tab
from agent.channels import Channel, Deliver
from agent.connectors import Connector, Connectors, Kind
from agent.messages import Address, Message, Text
from agent.prompts import DEFAULTS, Prompt, Prompts
from agent.store import Line, Step, Store, Task
from agent.twin.graph import Graph, Link
from agent.web import Web

log = logging.getLogger(__name__)

FOREVER = "max-age=31536000, immutable"  # an image never changes under its name
HEARTBEAT_SECONDS = 1


class PanelChannel(Channel):
    name = "panel"
    shows = "text and pictures"
    owner = Address(name, "owner")

    def __init__(self, store: Store, prompts: Prompts, connectors: Connectors, graph: Graph, web: Web):
        self._deliver: Deliver | None = None
        self._web = web
        _routes(web.app, self, store, prompts)
        _connector_routes(web.app, connectors)
        _twin_routes(web.app, graph)
        if not web.public:  # the twin's evals: for working on it locally, never on the internet
            evals_tab.routes(web.app)
        web.share(lambda path: _public_report(store, path))

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


class Visibility(BaseModel):
    public: bool


class NewConnector(BaseModel):
    kind: Kind
    settings: dict[str, str] = {}
    profile: str = ""


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
        return [_task(task, store.has_page(task.id)) for task in store.tasks()]

    @app.get("/api/tasks/{id}/steps")
    async def steps(id: int, after: int = 0) -> list[Step]:
        return store.steps(id, after)

    @app.put("/api/tasks/{id}/public")
    async def make_public(id: int, visibility: Visibility) -> dict:
        if not store.has_page(id):
            raise HTTPException(404, "No such report")
        store.make_public(id, visibility.public)
        return visibility.model_dump()

    @app.get("/api/images/{name}")
    async def image(name: str) -> Response:
        if not (found := store.image(name)):
            raise HTTPException(404, "No such image, or it has expired")
        return Response(found.data, media_type=found.media_type, headers={"Cache-Control": FOREVER})

    @app.get("/reports/{id}")
    async def report(id: int) -> HTMLResponse:
        if (page := store.page(id)) is None:
            raise HTTPException(404, "No such report")
        # Written by a model from what the web said: show it, but let nothing in it run or load.
        return HTMLResponse(page, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

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


def _connector_routes(app: FastAPI, connectors: Connectors) -> None:
    @app.get("/api/connectors")
    async def listed() -> dict:
        return {"services": connectors.services(), "connectors": [_connector(c) for c in connectors.all()]}

    @app.post("/api/connectors")
    async def add(new: NewConnector) -> dict:
        try:
            connector = connectors.add(new.kind, new.settings, new.profile)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return await sign_in(connector.name)

    @app.post("/api/connectors/{name}/sign-in")
    async def sign_in(name: str) -> dict:
        """Where to send the owner to sign in; none if they still are."""
        try:
            return {"signIn": await connectors.sign_in(name)}
        except KeyError as error:
            raise HTTPException(404, f"No connector called {name!r}") from error
        except Exception as error:  # the service: down, or refusing
            log.exception("Couldn't start signing in to %s", name)
            raise HTTPException(502, f"Couldn't reach it: {error}") from error

    @app.delete("/api/connectors/{name}")
    async def remove(name: str) -> None:
        connectors.remove(name)

    @app.get("/connectors/callback")
    async def callback(state: str = "", code: str = "", error: str = "", iss: str | None = None) -> HTMLResponse:
        if error or not code:
            return _done(f"Not connected: {error or 'no sign-in'}.", 400)
        try:
            connector = await connectors.finish(state, code, iss)
        except Exception as failure:
            log.exception("Couldn't finish signing in")
            return _done(f"Not connected: {failure}", 400)
        return _done(f"{connector.title} is connected.")


async def _events(channel: PanelChannel) -> AsyncIterator[str]:
    # Ends itself on shutdown: the server waits for open responses, and this one would never finish.
    async with aclosing(activity.listen(idle=HEARTBEAT_SECONDS)) as notices:
        async for notice in notices:
            if channel.stopping:
                return
            yield f"data: {json.dumps(asdict(notice))}\n\n" if notice else ": still here\n\n"


class Page(BaseModel):
    page: str


class NewNode(BaseModel):
    name: str
    page: str = ""


class Name(BaseModel):
    name: str


class NewLink(BaseModel):
    source: str
    target: str
    relation: str = ""
    preview: str = ""


def _twin_routes(app: FastAPI, graph: Graph) -> None:
    # The owner's own edits: no words to quote, made on the panel. Previews of links to an edited page go stale
    # until the writer's next run, which can't end before it rewrites them.

    @app.get("/api/twin")
    async def whole() -> dict:
        stale = {(link.source, link.target) for link in graph.stale()}
        return {
            "nodes": [{"name": n.name, "aliases": n.aliases, "router": not n.page.strip()} for n in graph.nodes()],
            "links": [_link(link, (link.source, link.target) in stale) for link in graph.links()],
        }

    @app.get("/api/twin/nodes/{name}")
    async def node(name: str) -> dict:
        found = _found(graph, name)
        stale = {(link.source, link.target) for link in graph.stale()}
        return {
            "name": found.name,
            "aliases": found.aliases,
            "page": found.page,
            "from": [_link(link, (link.source, link.target) in stale) for link in graph.links_from(found.name)],
            "to": [_link(link, (link.source, link.target) in stale) for link in graph.links_to(found.name)],
            "history": [asdict(change) for change in graph.history(found.name)],
        }

    @app.post("/api/twin/nodes")
    async def add(new: NewNode) -> None:
        _refused(lambda: graph.add(new.name, new.page, origin="panel"))

    @app.put("/api/twin/nodes/{name}")
    async def write(name: str, page: Page) -> None:
        graph.write(_found(graph, name).name, page.page, origin="panel")

    @app.put("/api/twin/nodes/{name}/name")
    async def rename(name: str, new: Name) -> None:
        _refused(lambda: graph.rename(_found(graph, name).name, new.name, origin="panel"))

    @app.post("/api/twin/nodes/{name}/aliases")
    async def alias(name: str, alias: Name) -> None:
        _refused(lambda: graph.alias(_found(graph, name).name, alias.name, origin="panel"))

    @app.delete("/api/twin/aliases/{alias}")
    async def unalias(alias: str) -> None:
        _found(graph, alias)
        _refused(lambda: graph.unalias(alias, origin="panel"))

    @app.delete("/api/twin/nodes/{name}")
    async def delete(name: str) -> None:
        _refused(lambda: graph.delete(_found(graph, name).name, origin="panel"))

    @app.put("/api/twin/links")
    async def link(new: NewLink) -> None:
        """Adds a link, or rewrites one's relation and preview."""
        _refused(lambda: graph.link(new.source, new.target, new.relation, new.preview, origin="panel"))

    @app.delete("/api/twin/links")
    async def unlink(source: str, target: str) -> None:
        _refused(lambda: graph.unlink(source, target, origin="panel"))


def _found(graph: Graph, name: str):
    if not (node := graph.find(name)):
        raise HTTPException(404, f"No node called {name!r}")
    return node


def _refused(edit) -> None:
    """What the graph won't do (a taken name, the root) goes back to the owner as the reason."""
    try:
        edit()
    except ValueError as error:
        raise HTTPException(400, str(error))


def _link(link: Link, stale: bool) -> dict:
    return {**asdict(link), "stale": stale}


def _public_report(store: Store, path: str) -> bool:
    id = path.removeprefix("/reports/")
    return path != id and id.isdigit() and bool((task := store.task(int(id))) and task.public)


def _task(task: Task, page: bool) -> dict:
    return {
        "id": task.id,
        "goal": task.goal,
        "kind": task.kind,
        "page": page,
        "public": task.public,
        "status": task.status,
        "open": task.status.open,
        "report": task.report,
        "reportTo": task.report_to.channel,
    }


def _connector(connector: Connector) -> dict:  # never the secrets or tokens
    return {
        "name": connector.name,
        "kind": connector.kind,
        "title": connector.title,
        "account": connector.account,
        "profile": connector.profile,
        "signedIn": connector.signed_in,
    }


def _done(text: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        '<!DOCTYPE html><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<p style="font:16px system-ui;margin:2rem">{escape(text)} <a href="/connectors">Back to Archangel</a></p>',
        status,
    )


def _prompt(prompt: Prompt) -> dict:
    return {"name": prompt.name, "text": prompt.text, "default": prompt.default, "edited": prompt.edited}


def _edit(prompts: Prompts, name: str, text: str | None) -> Prompt:
    if name not in DEFAULTS:
        raise HTTPException(404, f"No prompt called {name!r}")
    prompts.edit(name, text)
    return prompts.get(name)
