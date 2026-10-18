"""Archangel's one web server. Channels that listen over HTTP (the panel, Twilio) add their routes to its app.

It speaks for the owner. Locally it binds to 127.0.0.1 and refuses any other Host header: a web page can't reach
it by pointing its own domain at this machine (DNS rebinding). It only listens publicly with GitHub sign-in, and
then serves the built panel (apps/panel/dist) too, so the browser sees one origin.
"""

import asyncio
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent.channels.login import GitHub, protect

log = logging.getLogger(__name__)

PORT = 8765
DIST = Path("../panel/dist")  # from apps/agent
HOSTS = ["localhost", "127.0.0.1"]
PAGES = ["/chat", "/tasks", "/tasks/{id}", "/cockpit", "/prompts", "/connectors"]  # the panel's, as in its route.tsx


class Web:
    def __init__(self, github: GitHub | None = None, port: int = PORT):
        """With `github`, public behind sign-in; without, local only."""
        self.public = github is not None
        self._port = port
        self._server: uvicorn.Server | None = None
        self._closed = asyncio.Event()
        self.app = FastAPI(title="Archangel")
        if github:
            protect(self.app, github)
        else:
            self.app.add_middleware(TrustedHostMiddleware, allowed_hosts=HOSTS)

        @self.app.get("/health")
        async def health() -> dict:
            return {"ok": True}

    def finish(self) -> FastAPI:
        """The app, with the built panel mounted last so it can't shadow any channel's routes."""
        if DIST.is_dir() and not any(getattr(route, "name", None) == "panel" for route in self.app.routes):

            async def page() -> FileResponse:  # the app finds its page from the address
                return FileResponse(DIST / "index.html")

            for path in PAGES:
                self.app.add_api_route(path, page, include_in_schema=False)
            self.app.mount("/", StaticFiles(directory=DIST, html=True), name="panel")
        return self.app

    async def serve(self) -> None:
        host = "0.0.0.0" if self.public else "127.0.0.1"
        # Public means behind a proxy (Railway): trust its headers, so request.url is the https URL callers used.
        config = uvicorn.Config(
            self.finish(),
            host=host,
            port=self._port,
            log_level="warning",
            forwarded_allow_ips="*" if self.public else None,
        )
        log.info("Web on http://%s:%d", host, self._port)
        self._server = uvicorn.Server(config)
        try:
            await self._server.serve()
        except SystemExit:  # uvicorn's way of saying it couldn't start, usually the port
            raise RuntimeError(f"Couldn't listen on port {self._port}. Is Archangel already running?") from None
        finally:
            self._closed.set()

    @property
    def stopping(self) -> bool:
        return bool(self._server and self._server.should_exit)

    async def closed(self) -> None:
        """Wait until the server has stopped."""
        await self._closed.wait()
