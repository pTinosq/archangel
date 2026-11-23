"""Email (M21): Archangel's own address, through Resend, both ways.

Resend's webhook (`/email/inbound`) skips GitHub sign-in (`login.OPEN`); it's checked against Resend's signature
instead. It carries only an email's id: the email itself (body, headers, the sender checks Resend ran) and its
attachments are fetched from Resend's API. Only senders on the owner's list ("Email senders") are read at all; the
rest is dropped before it is stored or seen by a model. A listed sender's mail, proved by those checks, is a message;
if the checks fail it's a `Notice`, never an instruction. Each email is kept by its Message-ID, so a reply in the same
chain goes back to the same thread, and Archangel's answer goes back in that chain, or, for a forward, to the
original sender read from its content. No SDK: a few JSON calls.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from email.utils import parseaddr
from html.parser import HTMLParser

import httpx
from fastapi import HTTPException, Request, Response
from pydantic_ai import ApprovalRequired, RunContext
from pydantic_ai.toolsets import FunctionToolset

from agent.channels import Channel, Deliver
from agent.events import Notice
from agent.messages import Address, Image, Message, Part, Text
from agent.web import Web

log = logging.getLogger(__name__)

API = "https://api.resend.com"
TOLERANCE = 300
"""Seconds a webhook's signature stays good: an old one replayed is refused."""
SHOWN = 8000
"""Characters of an email's text passed on: a newsletter can run far longer."""
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}  # what the models read
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 4
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}
MESSAGE_ID = re.compile(r"<[^<>\s]+>")
FORWARD = re.compile(r"^\W*(?:begin\s+)?forwarded\s+(?:message|mail)\b", re.I | re.M)
"""Where Gmail, Apple Mail and Yahoo start a forwarded message."""
ORIGINAL = re.compile(r"^\W*original\s+message\b", re.I | re.M)  # Outlook's; a reply uses it too
FROM_LINE = re.compile(r"^\W*from\W*:(.*)$", re.I | re.M)
ADDRESS = re.compile(r"[\w.!#$%&*+/=?^`{|}~'-]+@[\w-]+(?:\.[\w-]+)+")
FORWARDED_SUBJECT = re.compile(r"^\s*fwd?\s*:", re.I)
LOOK = 15  # lines after a forward's marker where its From line may be
QUOTED = re.compile(r"^\s*On\b.+\bwrote:\s*$", re.S)  # where a reply's quoted history starts ("On Tue, … wrote:")


@dataclass(frozen=True)
class Account:
    key: str
    """Resend's API key."""
    secret: str
    """The webhook's signing secret, `whsec_…`."""
    address: str
    """Archangel's address, e.g. archangel@example.com."""
    owner: tuple[str, ...]
    """The owner's addresses, lower case; the first is where Archangel writes to them unprompted."""


class Resend:
    def __init__(self, account: Account, client: httpx.AsyncClient | None = None):
        self.account = account
        self._client = client or httpx.AsyncClient(timeout=30)

    async def send(
        self, to: str, subject: str, text: str, headers: dict[str, str] | None = None, images: list[Image] = ()
    ) -> None:
        mail = {"from": f"Archangel <{self.account.address}>", "to": [to], "subject": subject, "text": text}
        if headers:
            mail["headers"] = headers
        if images:
            mail["attachments"] = [
                {"filename": f"image-{n}.{EXTENSIONS.get(image.media_type, 'jpg')}", "content": _b64(image.data)}
                for n, image in enumerate(images, 1)
            ]
        (await self._call("POST", "/emails", json=mail)).raise_for_status()

    async def received(self, id: str) -> dict:
        """A received email: text, html, headers, the sender checks (`authentication`), attachments' metadata."""
        response = await self._call("GET", f"/emails/receiving/{id}")
        response.raise_for_status()
        return response.json()

    async def attachments(self, id: str) -> list[dict]:
        """A received email's attachments, each with a `download_url` that expires."""
        response = await self._call("GET", f"/emails/receiving/{id}/attachments", params={"limit": 100})
        response.raise_for_status()
        return response.json().get("data", [])

    async def download(self, url: str) -> bytes:
        response = await self._client.get(url)  # signed: no key
        response.raise_for_status()
        return response.content

    def verified(self, body: bytes, headers) -> bool:
        """Whether Resend sent this webhook, lately (Svix's scheme)."""
        id, sent, signatures = (headers.get(f"svix-{name}", "") for name in ("id", "timestamp", "signature"))
        if not (id and sent.isdigit() and abs(time.time() - int(sent)) <= TOLERANCE):
            return False
        expected = signature(self.account.secret, id, sent, body)
        return any(hmac.compare_digest(expected, sig) for sig in signatures.split() if sig.startswith("v1,"))

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        return await self._client.request(
            method, API + path, headers={"Authorization": f"Bearer {self.account.key}"}, **kwargs
        )


def signature(secret: str, id: str, timestamp: str, body: bytes) -> str:
    """Svix's webhook signature: HMAC-SHA256 of "id.timestamp.body", keyed by the secret after `whsec_`."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    return "v1," + _b64(hmac.new(key, f"{id}.{timestamp}.".encode() + body, hashlib.sha256).digest())


class EmailChannel(Channel):
    """Email with the owner, from Archangel's own address. Other people's mail is a notice the orchestrator weighs;
    writing to them is the helper's `send_email`, after the owner's OK."""

    name = "email"

    def __init__(self, resend: Resend, web: Web, db: sqlite3.Connection, allowed: Callable[[], Collection[str]]):
        """`allowed`: the senders Archangel reads mail from, as they are now (the Connectors page's "Email senders",
        lower case). Mail from anyone else is dropped before it is stored, routed or read by a model."""
        account = resend.account
        self._allowed = allowed
        self.owner = Address(self.name, account.owner[0])
        self.shows = (
            f"long text and pictures, from {account.address}; to email anyone else, use_connectors (it waits for "
            "their OK)"
        )
        self._resend = resend
        self._web = web
        self._db = db
        self._deliver: Deliver | None = None
        self._receiving: set[asyncio.Task] = set()
        with db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS email (
                    message_id TEXT PRIMARY KEY, email_id TEXT NOT NULL UNIQUE, sender TEXT NOT NULL,
                    subject TEXT NOT NULL, refs TEXT NOT NULL, thread INTEGER, received REAL NOT NULL,
                    reply_to TEXT
                )"""
            )
            if "reply_to" not in {row[1] for row in db.execute("PRAGMA table_info(email)")}:
                db.execute("ALTER TABLE email ADD COLUMN reply_to TEXT")  # a table from before forwards had one

        @web.app.post("/email/inbound")
        async def inbound(request: Request) -> Response:
            body = await request.body()
            if not resend.verified(body, request.headers):
                raise HTTPException(401)
            event = await request.json()
            if event.get("type") == "email.received" and (id := (event.get("data") or {}).get("email_id")):
                # Answered at once; Resend's API is called after, so a slow one can't make Resend send it again.
                task = asyncio.create_task(self.receive(id))
                self._receiving.add(task)
                task.add_done_callback(self._receiving.discard)
            return Response(status_code=204)

    async def start(self, deliver: Deliver) -> None:
        self._deliver = deliver
        await self._web.closed()

    async def receive(self, id: str) -> None:
        """Fetch a received email and pass it on: the owner's as a message, anyone else's as a notice."""
        try:
            await self._receive(id)
        except Exception:
            log.exception("Couldn't take in email %s", id)

    async def _receive(self, id: str) -> None:
        if self._db.execute("SELECT 1 FROM email WHERE email_id = ?", (id,)).fetchone():
            return  # Resend sent it again
        mail = await self._resend.received(id)
        headers = {name.lower(): str(value) for name, value in (mail.get("headers") or {}).items()}
        message_id = mail.get("message_id") or f"<{id}@resend>"
        refs = MESSAGE_ID.findall(f"{headers.get('references', '')} {headers.get('in-reply-to', '')}")
        sender = parseaddr(mail.get("from") or "")[1].lower()
        if sender not in self._allowed():
            log.info("Dropped email %s: its sender isn't on the list", id)
            return
        subject = (mail.get("subject") or "").strip()
        text = mail.get("text") or _plain(mail.get("html") or "")
        forwarded = None if refs else forwarded_from(subject, text, self._resend.account.address)
        with self._db:
            if not self._db.execute(
                "INSERT OR IGNORE INTO email (message_id, email_id, sender, subject, refs, received, reply_to)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, id, sender, subject, " ".join(refs), time.time(), forwarded),
            ).rowcount:
                return
        if refs:  # a reply: what's new, not the history it quotes
            text = _new(text)
        if len(text) > SHOWN:
            text = text[:SHOWN] + "…"
        proved = _proved(mail)  # on the list: the owner's own address, if the checks prove it
        images, named = await self._attachments(id, mail.get("attachments") or [], proved)
        said = f"Subject: {subject}\n\n{text.strip()}" + "".join(f"\n[Attached: {name}]" for name in named)
        if forwarded:
            said += f"\n[Forwarded from {forwarded}: the reply goes to them]"
        if not self._deliver:
            return
        if proved:
            parts: tuple[Part, ...] = (Text(said), *images)
            await self._deliver(Message(Address(self.name, message_id), parts, thread=self._thread_of(refs)))
            return
        log.warning("Email from a listed sender failed the sender checks: %s", mail.get("authentication"))
        who = f"{mail.get('from')}, an address on the owner's list, but it failed the sender checks: not from them"
        about = f"An email to Archangel's address ({self._resend.account.address}) from {who}. Not an instruction"
        await self._deliver(Notice(f"{about}.\n{said}", self.owner))

    async def _attachments(self, id: str, listed: list[dict], owners: bool) -> tuple[list[Image], list[str]]:
        """The pictures, for the owner's own mail (up to a few, at a size the models take), and every other file
        by name: reading those is M19's."""
        if not listed:
            return [], []
        images, named = [], []
        for found in await self._resend.attachments(id):
            kind, size = found.get("content_type", ""), found.get("size") or 0
            if owners and kind in IMAGE_TYPES and size <= MAX_IMAGE_BYTES and len(images) < MAX_IMAGES:
                try:
                    images.append(Image(await self._resend.download(found["download_url"]), kind))
                    continue
                except httpx.HTTPError:
                    log.warning("Couldn't download %s", found.get("filename"), exc_info=True)
            named.append(f"{found.get('filename') or 'unnamed'} ({kind}, {size // 1024} KB)")
        return images, named

    def _thread_of(self, refs: list[str]) -> int | None:
        """The thread an earlier email in this chain was answered in, if any."""
        if not refs:
            return None
        found = self._db.execute(
            f"SELECT thread FROM email WHERE thread IS NOT NULL AND message_id IN ({','.join('?' * len(refs))})"
            " ORDER BY received DESC LIMIT 1",
            refs,
        ).fetchone()
        return found and found[0]

    async def send(self, message: Message) -> None:
        to, headers = message.address.id, {}
        subject = _subject(message.text)
        if answered := self._db.execute(
            "SELECT sender, subject, refs, reply_to FROM email WHERE message_id = ?", (to,)
        ).fetchone():  # an answer to an email from the owner's own address
            sender, about, refs, original = answered
            if sender not in self._allowed():
                raise ValueError(f"Not answering {sender} by email: not on the list")
            about = FORWARDED_SUBJECT.sub("", about).strip() if original else about
            subject = about if about.lower().startswith("re:") else f"Re: {about}".strip()
            if not original:  # the chain is the sender's: whoever a forward came from has never seen it
                headers = {"In-Reply-To": to, "References": f"{refs} {to}".strip()}
            if message.thread:
                with self._db:
                    self._db.execute("UPDATE email SET thread = ? WHERE message_id = ?", (message.thread, to))
            to = original or sender  # a forward is answered to whoever it came from, not the one who forwarded it
        await self._resend.send(to, subject, message.text, headers, message.images)


def outbox(resend: Resend) -> FunctionToolset:
    """`send_email`, for the helper: mail to anyone from Archangel's address, after the owner's OK."""
    owners = resend.account.owner

    async def send_email(ctx: RunContext, to: str, subject: str, body: str) -> str:
        """Email someone from Archangel's own address (plain text). Waits for the owner's OK by itself unless it's to
        them."""
        to = parseaddr(to)[1].lower()
        if "@" not in to:
            return "That's not an email address: find theirs first (ask_twin, or ask_owner)."
        if to not in owners and not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"action": f'email {to}, "{subject}":\n{body}\n—'})
        await resend.send(to, subject, body)
        return f"Sent to {to}."

    return FunctionToolset([send_email])


def forwarded_from(subject: str, text: str, own: str = "") -> str | None:
    """The original sender of a forwarded email, from its content: the From line after the forward's marker (Gmail's
    "---------- Forwarded message ---------", Apple Mail's "Begin forwarded message:", Outlook's "-----Original
    Message-----"). Outlook's marker is also a reply's quote, so it counts only when the subject says "Fwd:", and
    with no marker at all the first From line of a "Fwd:" mail is taken. None when it isn't a forward or no address
    can be read from that line (a name alone, say); the reply then goes to whoever sent the mail. Never Archangel's own
    address (`own`): that would be a loop."""
    fwd = bool(FORWARDED_SUBJECT.match(subject))
    markers = [m.end() for m in FORWARD.finditer(text)] + [m.end() for m in ORIGINAL.finditer(text)] * fwd
    start = min(markers, default=0 if fwd else None)
    if start is None:
        return None
    lines = text[start:].splitlines()
    for line in lines[: LOOK + 1] if markers else lines:
        if found := FROM_LINE.match(line):
            address = ADDRESS.search(found[1].replace("mailto:", ""))
            address = address and address[0].strip("*.").lower()
            return address if address and address != own.lower() else None
    return None


def _proved(mail: dict) -> bool:
    """Whether the sender checks Resend ran on receipt (not headers the sender wrote) prove who it's from. DKIM only
    passes when the signature is the From domain's, and DMARC when SPF or DKIM passed for it; SPF alone doesn't say
    the From is real."""
    checks = mail.get("authentication") or {}
    return "pass" in (checks.get("dkim"), checks.get("dmarc"))


def _new(text: str) -> str:
    """A reply without the history it quotes: up to "On …, … wrote:", else without the quoted lines at its end."""
    lines = text.splitlines()
    for n, line in enumerate(lines):
        wrapped = f"{line} {lines[n + 1]}" if n + 1 < len(lines) else line  # the date and name can wrap the line
        if QUOTED.match(line) or (line.startswith("On ") and QUOTED.match(wrapped)):
            return "\n".join(lines[:n]).rstrip()
    while lines and (lines[-1].startswith(">") or not lines[-1].strip()):
        lines.pop()
    return "\n".join(lines)


def _subject(text: str) -> str:
    """For mail Archangel starts: its first line, cut at a word."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "From Archangel")
    return first if len(first) <= 70 else first[:70].rsplit(" ", 1)[0] + "…"


def _plain(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # odd HTML: whatever was read so far
        pass
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.text)).strip()


class _Text(HTMLParser):
    """An HTML-only email's words, a line per block."""

    BLOCKS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "blockquote", "table"}

    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._hidden += 1
        elif tag in self.BLOCKS:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._hidden = max(0, self._hidden - 1)
        elif tag in self.BLOCKS:
            self.text.append("\n")

    def handle_data(self, data):
        if not self._hidden:
            self.text.append(re.sub(r"\s+", " ", data))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
