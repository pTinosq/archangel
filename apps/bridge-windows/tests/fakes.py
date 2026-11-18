from bridge.device import CommandError, Ran, Screen, Shot
from bridge.protocol import ErrorCode, Message


class FakeDevice:
    """Records what it's asked to do."""

    def __init__(self):
        self.calls: list[str] = []
        self.apps_listed = [{"label": "Notepad", "package": "notepad"}]

    def screen(self) -> Screen:
        return Screen(1920, 1080, 1.25)

    def mac(self) -> str | None:
        return "a8:a1:59:12:34:56"

    async def screenshot(self, scale: float, quality: int) -> Shot:
        self.calls.append(f"screenshot {scale} {quality}")
        return Shot(round(1920 * scale), round(1080 * scale), b"jpeg")

    async def tap(self, x, y, duration_ms, button, count):
        self.calls.append(f"tap {x},{y} {duration_ms}ms {button} x{count}")

    async def swipe(self, x1, y1, x2, y2, duration_ms):
        self.calls.append(f"swipe {x1},{y1} -> {x2},{y2} {duration_ms}ms")

    async def scroll(self, x, y, dx, dy):
        self.calls.append(f"scroll {x},{y} by {dx},{dy}")

    async def type(self, text):
        self.calls.append(f"type {text}")

    async def key(self, key):
        self.calls.append(f"key {key}")

    async def launch(self, app):
        if app not in (found["package"] for found in self.apps_listed):
            raise CommandError(ErrorCode.NOT_FOUND, f"No app with id {app!r}")
        self.calls.append(f"launch {app}")

    async def apps(self) -> list[Message]:
        return self.apps_listed

    async def ui_tree(self) -> list[Message]:
        return [{"class": "Window", "bounds": [0, 0, 1920, 1080]}]

    async def unlock(self):
        raise CommandError(ErrorCode.FAILED, "No PIN or password is set in the bridge's settings")

    async def powershell(self, script):
        self.calls.append(f"powershell {script}")
        if "Start-Sleep" in script:
            return Ran(None, "", "")
        return Ran(0, "hello\n", "")
