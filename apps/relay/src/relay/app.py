import base64
import hmac
import logging

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from relay import config
from relay.hub import Disconnected, Hub, Message, Offline

log = logging.getLogger("uvicorn.error")


class Command(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    id: str | None = None


def error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status_code)


def create_app(token: str | None = None, timeout: float = config.COMMAND_TIMEOUT) -> FastAPI:
    expected = f"Bearer {token or config.token()}"
    hub = Hub(timeout)
    app = FastAPI(title="Archangel relay")

    def authorized(header: str | None) -> bool:
        return hmac.compare_digest(header or "", expected)

    def require_auth(request: Request) -> None:
        if not authorized(request.headers.get("authorization")):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    @app.exception_handler(Offline)
    def offline(_: Request, __: Offline) -> JSONResponse:
        return error(503, "offline", "Device is not connected")

    @app.exception_handler(Disconnected)
    def disconnected(_: Request, __: Disconnected) -> JSONResponse:
        return error(502, "disconnected", "Device disconnected before answering; outcome unknown")

    @app.exception_handler(TimeoutError)
    def timed_out(_: Request, __: TimeoutError) -> JSONResponse:
        return error(504, "timeout", f"No answer within {timeout:g}s; outcome unknown")

    @app.get("/health")
    def health() -> Message:
        return {"ok": True}

    @app.get("/devices", dependencies=[Depends(require_auth)])
    def devices() -> list[Message]:
        return [device.hello for device in hub.devices.values()]

    @app.post("/devices/{name}/commands", dependencies=[Depends(require_auth)])
    async def command(name: str, body: Command) -> Message:
        return await hub.send(name, body.model_dump(exclude_none=True))

    @app.get("/devices/{name}/screenshot.jpg", dependencies=[Depends(require_auth)])
    async def screenshot(name: str) -> Response:
        result = await hub.send(name, {"type": "screenshot"})
        if not result["ok"]:
            return JSONResponse(result, status_code=502)
        return Response(base64.b64decode(result["data"]["jpeg"]), media_type="image/jpeg")

    @app.websocket("/ws/device")
    async def device_socket(socket: WebSocket) -> None:
        if not authorized(socket.headers.get("authorization")):
            await socket.close(status.WS_1008_POLICY_VIOLATION)
            return

        await socket.accept()
        hello = await socket.receive_json()
        if hello.get("type") != "hello" or not hello.get("device"):
            await socket.close(status.WS_1008_POLICY_VIOLATION, "Expected hello")
            return

        device = await hub.attach(hello, socket)
        log.info("Device connected: %s", device.name)
        try:
            while True:
                hub.resolve(device, await socket.receive_json())
        except WebSocketDisconnect:
            pass
        finally:
            hub.detach(device)
            log.info("Device disconnected: %s", device.name)

    return app
