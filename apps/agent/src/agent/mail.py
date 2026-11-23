"""Reading a received mail: its parts, and, when the owner forwarded it, who wrote it originally.

Forwards have no standard form (a mail client quotes it, a header block, an attached message, in any language), so a
quick model reads the text for who the original sender is and where the owner's own words end; code checks what it
answers against the text, so it can't invent an address. When it can't be found there's no reply address: the
orchestrator asks the owner, and never guesses.
"""

import re
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from agent import logs
from agent.models import Instructions

INSTRUCTIONS = """You read a mail the owner sent to Archangel, their assistant, and say whether it is a forward of \
something someone else wrote (in any mail client or language: a quoted block, a header block, an attached message), \
and if so who wrote it. `sender` is the original author's email address exactly as it appears in the text, not the \
owner's and not a mailing list's; empty if you can't find it, and never a guess. `note` is the owner's own words \
above or around the forwarded part, copied exactly; empty if they wrote none. Not a forward: forwarded false, the \
rest empty."""

LIMIT = 20_000
"""Characters of a mail kept: a model reads it, and an essay of quoted replies is no more use."""


class Origin(BaseModel):
    forwarded: bool = Field(description="The owner forwarded something someone else wrote")
    sender: str = Field("", description="The original author's address, exactly as in the text; empty if not found")
    note: str = Field("", description="The owner's own words, copied exactly; empty if none")


@dataclass(frozen=True)
class Mail:
    sender: str
    """The address it came from, as written (From, the address alone)."""
    subject: str
    id: str
    text: str
    """The body, with any attached message's headers and words in line; HTML as plain text."""
    attachments: int = 0
    """Files that came with it, not read."""


def address_of(text: str) -> str | None:
    """The one email address in `text` ("Sam <sam@example.com>" or "sam@example.com"), or None."""
    found = getaddresses([text], strict=True)
    if len(found) != 1 or not re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+", address := found[0][1]):
        return None
    return address


def parse(raw: bytes) -> Mail | None:
    """A received mail, or None when it doesn't say plainly who it's from: one address in From."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if not (sender := address_of(str(message.get("From", "")))):
        return None
    text, files = _text(message), 0
    for part in message.walk():
        if part.get_content_disposition() == "attachment" and part.get_content_type() != "message/rfc822":
            files += 1
    return Mail(
        sender, str(message.get("Subject", "")).strip(), str(message.get("Message-ID", "")).strip(), text[:LIMIT], files
    )


def _text(message: Message) -> str:
    body = message.get_body(("plain", "html")) if isinstance(message, EmailMessage) else None
    text = _plain(body) if body else ""
    for part in message.walk():
        if part.get_content_type() == "message/rfc822":  # forwarded as an attachment: its headers say who wrote it
            for inner in part.get_payload():
                headers = "\n".join(f"{name}: {inner[name]}" for name in ("From", "To", "Date", "Subject") if inner[name])
                text += f"\n\n--- Attached message ---\n{headers}\n\n{_text(inner)}"
    return text.strip()


def _plain(part: EmailMessage) -> str:
    content = part.get_content()
    if part.get_content_type() != "text/html":
        return content
    reader = _Visible()
    reader.feed(content)
    return "\n".join(line for line in (l.strip() for l in "".join(reader.text).splitlines()) if line)


class _Visible(HTMLParser):
    BREAKS = {"br", "p", "div", "tr", "li", "blockquote", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        self._skip += tag in ("script", "style")
        if tag in self.BREAKS:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        self._skip -= tag in ("script", "style")
        if tag in self.BREAKS:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.text.append(data)


class Reader:
    """Who wrote what the owner forwarded."""

    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS):
        self._agent = Agent(
            model, name="mail reader", output_type=Origin, deps_type=str, instructions=instructions, retries=2
        )

        @self._agent.output_validator
        async def found(ctx: RunContext[str], origin: Origin) -> Origin:  # deps: the mail's text
            if origin.sender and (
                address_of(origin.sender) != origin.sender or origin.sender.lower() not in ctx.deps.lower()
            ):
                raise ModelRetry("That address isn't in the mail. Copy it exactly from the text, or leave it empty.")
            if origin.note and origin.note not in ctx.deps:
                raise ModelRetry("`note` must be copied exactly from the mail. Leave it empty if there is none.")
            return origin

    async def read(self, text: str) -> Origin:
        return (await logs.run(self._agent, text, deps=text)).output
