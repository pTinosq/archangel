import asyncio
import mimetypes
import readline  # noqa: F401  (line editing for input())
import tempfile
import threading
from pathlib import Path

from agent.channels import Channel, Deliver
from agent.messages import Address, Image, Message, Part, Text

PROMPT = "› "
IMAGE_COMMAND = "/image "


class CliChannel(Channel):
    """Talk to Archangel in the terminal. `/image <path> [text]` attaches an image."""

    name = "cli"
    shows = "text; pictures as file paths"
    owner = Address(name, "local")

    async def start(self, deliver: Deliver) -> None:
        print("Archangel. `/image <path> [text]` attaches an image. Ctrl-D to quit.")
        lines = _read_lines()
        while (line := await lines.get()) is not None:
            try:
                parts = parse(line)
            except (OSError, ValueError) as error:
                print(error)
                continue
            if parts:
                await deliver(Message(self.owner, parts))

    async def send(self, message: Message) -> None:
        lines = [message.text] + [f"[image: {_save(image)}]" for image in message.images]
        # Clear the waiting prompt, print the reply, then redraw the prompt.
        print("\r" + "\n".join(lines) + f"\n{PROMPT}", end="", flush=True)


def parse(line: str) -> tuple[Part, ...]:
    line = line.strip()
    if not line.startswith(IMAGE_COMMAND):
        return (Text(line),) if line else ()

    path, _, text = line.removeprefix(IMAGE_COMMAND).strip().partition(" ")
    file = Path(path).expanduser()
    media_type, _ = mimetypes.guess_type(file)
    if not media_type or not media_type.startswith("image/"):
        raise ValueError(f"Not an image: {file}")
    image = Image(file.read_bytes(), media_type)
    return (image, Text(text.strip())) if text.strip() else (image,)


def _save(image: Image) -> Path:
    """Terminals can't show images; write it where a click or `open` can."""
    suffix = mimetypes.guess_extension(image.media_type) or ""
    with tempfile.NamedTemporaryFile(prefix="archangel-", suffix=suffix, delete=False) as file:
        file.write(image.data)
    return Path(file.name)


def _read_lines() -> asyncio.Queue[str | None]:
    """Read stdin on a daemon thread, so Ctrl-C exits without waiting on input()."""
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str | None] = asyncio.Queue()

    def read() -> None:
        while True:
            try:
                line = input(PROMPT)
            except EOFError:
                line = None
            loop.call_soon_threadsafe(lines.put_nowait, line)
            if line is None:
                return

    threading.Thread(target=read, daemon=True).start()
    return lines
