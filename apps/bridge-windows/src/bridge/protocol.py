"""Wire types. The contract lives in docs/protocol.md; keep the two in step."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PLATFORM = "windows"
VERSION = "0.1.0"


class _Command(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str


class Screenshot(_Command):
    type: Literal["screenshot"]
    scale: float = Field(1.0, gt=0, le=1)
    quality: int = Field(80, ge=1, le=100)


class Tap(_Command):
    type: Literal["tap"]
    x: float
    y: float
    durationMs: int = Field(50, ge=0, le=10_000)
    button: Literal["left", "right"] = "left"
    count: Literal[1, 2] = 1


class Swipe(_Command):
    type: Literal["swipe"]
    x1: float
    y1: float
    x2: float
    y2: float
    durationMs: int = Field(300, ge=0, le=10_000)


class Scroll(_Command):
    type: Literal["scroll"]
    x: float
    y: float
    dx: int = Field(0, ge=-50, le=50)
    dy: int = Field(0, ge=-50, le=50)


class Type(_Command):
    type: Literal["type"]
    text: str


class Key(_Command):
    type: Literal["key"]
    key: str


class Launch(_Command):
    type: Literal["launch"]
    package: str


class Apps(_Command):
    type: Literal["apps"]


class UiTree(_Command):
    type: Literal["ui_tree"]


class Unlock(_Command):
    type: Literal["unlock"]


class PowerShell(_Command):
    type: Literal["powershell"]
    script: str = Field(min_length=1, max_length=6_000)  # encoded, it must fit a command line (32,767)


Command = Annotated[
    Screenshot | Tap | Swipe | Scroll | Type | Key | Launch | Apps | UiTree | Unlock | PowerShell,
    Field(discriminator="type"),
]
COMMAND = TypeAdapter(Command)
TYPES = {"screenshot", "tap", "swipe", "scroll", "type", "key", "launch", "apps", "ui_tree", "unlock", "powershell"}

Message = dict[str, Any]


class ErrorCode:
    INVALID_COMMAND = "invalid_command"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    INTERNAL = "internal"


def success(id: str, data: Message | None = None) -> Message:
    return {"id": id, "ok": True, **({"data": data} if data is not None else {})}


def failure(id: str, code: str, message: str) -> Message:
    return {"id": id, "ok": False, "error": {"code": code, "message": message}}


def hello(device: str, width: int, height: int, density: float, mac: str | None) -> Message:
    message: Message = {
        "type": "hello",
        "device": device,
        "platform": PLATFORM,
        "screen": {"width": width, "height": height, "density": density},
        "version": VERSION,
    }
    if mac:
        message["wake"] = {"mac": mac}
    return message


def node(
    class_name: str,
    bounds: tuple[int, int, int, int],
    text: str = "",
    desc: str = "",
    id: str = "",
    flags: list[str] | None = None,
    children: list[Message] | None = None,
) -> Message:
    """A ui_tree node, empty fields omitted."""
    out: Message = {"class": class_name}
    if text:
        out["text"] = text
    if desc:
        out["desc"] = desc
    if id:
        out["id"] = id
    out["bounds"] = list(bounds)
    if flags:
        out["flags"] = flags
    if children:
        out["children"] = children
    return out
