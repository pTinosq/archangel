import asyncio
import base64
import json
import logging
from collections import OrderedDict

from pydantic import ValidationError

from bridge import protocol
from bridge.device import CommandError, Device
from bridge.protocol import ErrorCode, Message

log = logging.getLogger(__name__)

REMEMBERED_REPLIES = 64


class Executor:
    """Runs commands one at a time and never raises: every frame gets a reply.
    Replies are remembered by id, so a retried command is answered, not re-executed."""

    def __init__(self, device: Device):
        self._device = device
        self._lock = asyncio.Lock()
        self._recent: OrderedDict[str, Message] = OrderedDict()

    async def execute(self, frame: str) -> Message:
        async with self._lock:
            try:
                command = json.loads(frame)
                id = command["id"]
                if not isinstance(id, str):
                    raise TypeError
            except (ValueError, TypeError, KeyError):
                return protocol.failure("", ErrorCode.INVALID_COMMAND, "Expected a JSON object with an id")
            if (reply := self._recent.get(id)) is not None:
                return reply
            reply = await self._run(id, command)
            self._recent[id] = reply
            while len(self._recent) > REMEMBERED_REPLIES:
                self._recent.popitem(last=False)
            return reply

    async def _run(self, id: str, command: Message) -> Message:
        if command.get("type") not in protocol.TYPES:
            return protocol.failure(id, ErrorCode.INVALID_COMMAND, f"Not a command on windows: {command.get('type')}")
        try:
            parsed = protocol.COMMAND.validate_python(command)
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first["loc"][1:])  # [0] is the command type
            message = f"{where}: {first['msg']}" if where else first["msg"]
            return protocol.failure(id, ErrorCode.INVALID_COMMAND, message)
        try:
            return protocol.success(id, await self._perform(parsed))
        except asyncio.CancelledError:
            raise
        except CommandError as error:
            log.info("%s failed: %s", parsed.type, error)
            return protocol.failure(id, error.code, str(error))
        except Exception as error:
            log.exception("%s failed unexpectedly", parsed.type)
            return protocol.failure(id, ErrorCode.INTERNAL, f"{type(error).__name__}: {error}")

    async def _perform(self, command: protocol.Command) -> Message | None:
        device = self._device
        match command:
            case protocol.Screenshot(scale=scale, quality=quality):
                shot = await device.screenshot(scale, quality)
                return {"width": shot.width, "height": shot.height, "jpeg": base64.b64encode(shot.jpeg).decode()}
            case protocol.Tap(x=x, y=y, durationMs=duration, button=button, count=count):
                await device.tap(round(x), round(y), duration, button, count)
            case protocol.Swipe(x1=x1, y1=y1, x2=x2, y2=y2, durationMs=duration):
                await device.swipe(round(x1), round(y1), round(x2), round(y2), duration)
            case protocol.Scroll(x=x, y=y, dx=dx, dy=dy):
                await device.scroll(round(x), round(y), dx, dy)
            case protocol.Type(text=text):
                await device.type(text)
            case protocol.Key(key=key):
                await device.key(key)
            case protocol.Launch(package=app):
                await device.launch(app)
            case protocol.Apps():
                return {"apps": await device.apps()}
            case protocol.UiTree():
                return {"windows": await device.ui_tree()}
            case protocol.Unlock():
                await device.unlock()
        return None
