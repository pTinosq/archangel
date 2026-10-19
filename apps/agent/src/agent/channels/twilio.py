"""Twilio: the owner texts and calls Archangel's number, and it texts and rings them back.

Twilio's webhooks skip GitHub sign-in (`login.OPEN`); each one is checked against Twilio's signature instead, and
anything not from the owner's number is ignored. Twilio's REST API is a couple of form posts, so no SDK.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape, quoteattr

import httpx
from fastapi import Request, Response, WebSocket, WebSocketDisconnect

from agent.channels import Channel, Deliver
from agent.events import Notice
from agent.links import Links
from agent.messages import Address, Message, Text
from agent.web import Web

log = logging.getLogger(__name__)

API = "https://api.twilio.com/2010-04-01"
SMS_LIMIT = 1600  # Twilio's cap for one message, however many parts it's sent in
MORE = "… (more in the panel)"
EMPTY = "<Response/>"  # TwiML for "nothing to do": replies are sent separately
REJECT = "<Response><Reject/></Response>"
HANG_UP = "<Response><Hangup/></Response>"
UNANSWERED = {"busy", "no-answer", "failed", "canceled"}
GREETING = "Archangel."
RING_SECONDS = 120  # a call Archangel makes is answered or given up on well within this
RINGS = 3  # at most this many calls Archangel makes…
RINGS_SECONDS = 600  # …in this long; beyond that, it texts
PAUSE_SECONDS = 1.0  # Twilio splits what's said at a breath; speech this close together is one thing said
WORDS_PER_SECOND = 2.5  # about how fast it speaks: Twilio doesn't say when it's finished, so hang up after that


@dataclass(frozen=True)
class Account:
    sid: str
    token: str
    number: str
    """Archangel's Twilio number, E.164 (+44…)."""
    owner: str
    """The owner's mobile, E.164: the only number Archangel answers, and the one it texts and rings."""


class Twilio:
    def __init__(self, account: Account, client: httpx.AsyncClient | None = None):
        self.account = account
        self._client = client or httpx.AsyncClient(timeout=15)

    async def send_sms(self, to: str, text: str) -> None:
        await self._post("Messages.json", {"To": to, "From": self.account.number, "Body": text})

    async def call(self, to: str, answered: str, machine: str, ended: str) -> str:
        """Ring `to`. Twilio asks `answered` what to do as soon as anyone picks up, tells `machine` whether that was a
        person or a voicemail (in the background, so a person isn't left in silence), and `ended` how it went.
        Returns the call's id."""
        form = {"To": to, "From": self.account.number, "Url": answered, "StatusCallback": ended}
        # DetectMessageEnd: for a voicemail, the verdict comes after the beep, in time to leave a message.
        amd = {"MachineDetection": "DetectMessageEnd", "AsyncAmd": "true", "AsyncAmdStatusCallback": machine}
        return (await self._post("Calls.json", form | amd))["sid"]

    async def redirect(self, call: str, twiml: str) -> None:
        """Have a call in progress do something else."""
        await self._post(f"Calls/{call}.json", {"Twiml": twiml})

    async def _post(self, path: str, form: dict[str, str]) -> dict:
        url = f"{API}/Accounts/{self.account.sid}/{path}"
        response = await self._client.post(url, data=form, auth=(self.account.sid, self.account.token))
        response.raise_for_status()
        return response.json()

    async def signed(self, request: Request) -> dict[str, str] | None:
        """The form Twilio posted, or None if Twilio didn't send it."""
        form = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))  # always urlencoded
        expected = signature(self.account.token, str(request.url), form)
        return form if hmac.compare_digest(expected, request.headers.get("x-twilio-signature", "")) else None


def signature(token: str, url: str, form: dict[str, str]) -> str:
    """Twilio's request signature: the URL, then each form field's name and value in name order, HMAC-SHA1."""
    signed = url + "".join(name + form[name] for name in sorted(form))
    return base64.b64encode(hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()).decode()


class SmsChannel(Channel):
    """Text messages with the owner. UK numbers can't send pictures, so screenshots are a link to the panel."""

    name = "sms"
    shows = "short texts; screenshots as a link"

    def __init__(self, twilio: Twilio, web: Web, links: Links | None):
        self.owner = Address(self.name, twilio.account.owner)
        self._twilio = twilio
        self._web = web
        self._links = links
        self._deliver: Deliver | None = None

        @web.app.post("/twilio/sms")
        async def received(request: Request) -> Response:
            if (form := await twilio.signed(request)) is None:
                return Response(status_code=403)
            sender, body = form.get("From", ""), form.get("Body", "").strip()
            if sender != twilio.account.owner:
                log.warning("Ignored a text from %s", sender)
            elif body and self._deliver:
                await self._deliver(Message(Address(self.name, sender), (Text(body),)))
            return _xml(EMPTY)

    async def start(self, deliver: Deliver) -> None:
        self._deliver = deliver
        await self._web.closed()

    async def send(self, message: Message) -> None:
        text = message.text
        for image in message.images:
            text += f"\n\nScreenshot: {self._links.to(image)}" if self._links else "\n\nScreenshot in the panel."
        if len(text) > SMS_LIMIT:
            text = text[: SMS_LIMIT - len(MORE)] + MORE
        await self._twilio.send_sms(message.address.id, text)


@dataclass
class _Outgoing:
    """A call Archangel is making."""

    started: float
    said: list[str]
    """What it rang to say, plus anything that came up before it was live."""
    person: bool | None = None
    """Who answered, once Twilio knows: a person, or a voicemail (None: not yet known)."""
    heard: list[str] = field(default_factory=list)
    """What was heard before Twilio knew it was a person: a voicemail greeting mustn't be taken for the owner."""
    greeted: int = 0
    """How much of `said` was the greeting when they picked up."""
    voicemail: str = ""
    """The message left after the beep."""


class VoiceChannel(Channel):
    """Phone calls with the owner. Twilio's ConversationRelay does the listening and speaking; this trades text with it
    over a WebSocket.

    While the owner's on a call, everything is said on it. Otherwise Archangel rings only if they asked for a call back
    (the message is addressed to them, not to a call), a few times in ten minutes at most. The message is said as soon
    as they pick up; if it's their voicemail, it's left after the beep instead. How a call went (voicemail, no answer)
    is a notice to the orchestrator, which decides what to text. News for a call that has ended is texted, but replies
    to what was said on it aren't: hanging up after "bye" shouldn't bring a text saying "Goodbye."
    """

    name = "voice"

    @property
    def shows(self) -> str:
        live = "they're on a call now" if self._calls else "no call now: a message rings them, and may reach voicemail"
        return f"speech, screenshots texted as a link; {live}"

    def __init__(self, twilio: Twilio, web: Web, public_url: str, links: Links):
        self.owner = Address(self.name, twilio.account.owner)
        self._twilio = twilio
        self._web = web
        self._url = public_url
        self._links = links
        # The URL Twilio signs the handshake with is the one we give it; behind a proxy, request.url isn't that.
        self._relay = public_url.replace("https://", "wss://", 1) + "/twilio/voice/relay"
        self._calls: dict[str, WebSocket] = {}  # live, by call id
        self._hanging_up: dict[WebSocket, asyncio.Task] = {}  # calls to end once the last word is said
        self._outgoing: dict[str, _Outgoing] = {}  # calls Archangel is making, until they end
        self._rung: deque[float] = deque(maxlen=RINGS)
        self._deliver: Deliver | None = None

        @web.app.post("/twilio/voice")
        async def ringing(request: Request) -> Response:
            if (form := await twilio.signed(request)) is None:
                return Response(status_code=403)
            if (caller := form.get("From", "")) != twilio.account.owner:
                log.warning("Rejected a call from %s", caller)
                return _xml(REJECT)
            return _xml(self._twiml(GREETING))

        @web.app.post("/twilio/voice/answered")
        async def answered(request: Request) -> Response:
            if (form := await twilio.signed(request)) is None:
                return Response(status_code=403)
            if not (outgoing := self._outgoing.get(form.get("CallSid", ""))):
                return _xml(self._twiml(GREETING))
            outgoing.greeted = len(outgoing.said)
            return _xml(self._twiml(" ".join(outgoing.said)))

        @web.app.post("/twilio/voice/machine")
        async def machine(request: Request) -> Response:
            if (form := await twilio.signed(request)) is None:
                return Response(status_code=403)
            call, answered_by = form.get("CallSid", ""), form.get("AnsweredBy", "unknown")
            if not (outgoing := self._outgoing.get(call)):
                return _xml(EMPTY)
            outgoing.person = answered_by in ("human", "unknown")
            if outgoing.person:
                for said in outgoing.heard:
                    await self._heard(call, said)
            elif answered_by.startswith("machine_end"):  # after the beep
                outgoing.voicemail = " ".join(outgoing.said)
                log.info("Reached voicemail; leaving the message")
                say = f'<Say language="en-GB">{escape(outgoing.voicemail)}</Say>'
                await twilio.redirect(call, f'<Response><Pause length="1"/>{say}<Hangup/></Response>')
            else:  # a fax, or a machine it couldn't wait out
                await twilio.redirect(call, HANG_UP)
            return _xml(EMPTY)

        @web.app.post("/twilio/voice/ended")
        async def ended(request: Request) -> Response:
            if (form := await twilio.signed(request)) is None:
                return Response(status_code=403)
            outgoing, status = self._outgoing.pop(form.get("CallSid", ""), None), form.get("CallStatus", "")
            if outgoing and outgoing.voicemail:
                await self._notice(f'Your call to the owner went to their voicemail. You left: "{outgoing.voicemail}"')
            elif outgoing and (status in UNANSWERED or outgoing.person is False):
                await self._notice(
                    f'You rang the owner; no answer ({status}). They haven\'t heard: "{" ".join(outgoing.said)}"'
                )
            elif outgoing and outgoing.person is None:  # picked up, but by whom Twilio never said
                await self._notice(
                    f"You rang the owner and it was picked up, but maybe by their voicemail before the beep, so they "
                    f'may not have heard: "{" ".join(outgoing.said)}"'
                )
            return _xml(EMPTY)

        @web.app.websocket("/twilio/voice/relay")
        async def relay(websocket: WebSocket) -> None:
            expected = signature(twilio.account.token, self._relay, {})
            if not hmac.compare_digest(expected, websocket.headers.get("x-twilio-signature", "")):
                await websocket.close(code=1008)
                return
            await websocket.accept()
            call, heard = "", []
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(websocket.receive_json(), PAUSE_SECONDS if heard else None)
                    except TimeoutError:  # they've finished: all of it is one thing said, and gets one reply
                        await self._hear(call, " ".join(heard))
                        heard.clear()
                        continue
                    if event.get("type") == "setup":
                        call = event.get("callSid", "")
                        self._calls[call] = websocket
                        if outgoing := self._outgoing.get(call):  # what came up while it connected
                            for text in outgoing.said[outgoing.greeted :]:
                                await self._say(websocket, text)
                    elif event.get("type") == "prompt" and (said := event.get("voicePrompt", "").strip()):
                        if hanging_up := self._hanging_up.pop(websocket, None):  # they're still talking
                            hanging_up.cancel()
                        heard.append(said)
                    elif event.get("type") == "error":
                        log.warning("Call %s: %s", call, event.get("description"))
            except WebSocketDisconnect:
                pass
            finally:
                self._calls.pop(call, None)
                self._hanging_up.pop(websocket, None)
                if heard:  # said just before hanging up
                    await self._hear(call, " ".join(heard))

    def _twiml(self, greeting: str) -> str:
        return (
            f'<Response><Connect><ConversationRelay url={quoteattr(self._relay)} language="en-GB"'
            f" welcomeGreeting={quoteattr(greeting)}/></Connect></Response>"
        )

    async def start(self, deliver: Deliver) -> None:
        self._deliver = deliver
        await self._web.closed()

    async def send(self, message: Message) -> None:
        text = message.text
        if message.images:  # pictures can't be heard: text them, and say so
            await self._text("\n".join(f"Screenshot: {self._links.to(image)}" for image in message.images))
            text += " I've texted you a screenshot."
        to = message.address.id
        if (call := self._calls.get(to) or next(iter(self._calls.values()), None)) and await self._say(call, text):
            if message.ends:
                self._hanging_up[call] = asyncio.create_task(self._hang_up(call, text))
            return
        if to == self.owner.id:
            await self._ring(text)
        elif message.answering:
            log.info("Not texting a reply to a call that has ended: %s", text)
        else:
            await self._text(text)

    async def _hear(self, call: str, said: str) -> None:
        outgoing = self._outgoing.get(call)
        if outgoing and outgoing.person is None:
            outgoing.heard.append(said)  # until Twilio knows it's them, not their voicemail
        elif not outgoing or outgoing.person:
            await self._heard(call, said)

    async def _hang_up(self, call: WebSocket, said: str) -> None:
        await asyncio.sleep(len(said.split()) / WORDS_PER_SECOND + 0.5)
        self._hanging_up.pop(call, None)
        with contextlib.suppress(Exception):  # already gone
            await call.send_text(json.dumps({"type": "end"}))  # nothing follows <Connect>, so the call ends

    async def _heard(self, call: str, said: str) -> None:
        if self._deliver:
            await self._deliver(Message(Address(self.name, call), (Text(said),)))

    async def _notice(self, text: str) -> None:
        if self._deliver:  # texts are where the owner will see it now
            await self._deliver(Notice(text, Address(SmsChannel.name, self.owner.id)))

    async def _say(self, call: WebSocket, text: str) -> bool:
        try:
            await call.send_text(json.dumps({"type": "text", "token": text, "last": True}))
            return True
        except Exception:  # the call dropped as we spoke
            log.warning("Couldn't say it on the call", exc_info=True)
            return False

    async def _ring(self, text: str) -> None:
        now = time.monotonic()
        # A call whose end Twilio never told us about (a deploy at the wrong moment) mustn't swallow what comes next.
        self._outgoing = {call: out for call, out in self._outgoing.items() if now - out.started < RING_SECONDS}
        if ringing := next((out for out in self._outgoing.values() if out.person is not False), None):
            ringing.said.append(text)  # already calling them: say it on that call too
            return
        if len(self._rung) == RINGS and now - self._rung[0] < RINGS_SECONDS:
            log.warning("Rung %d times in %d s; texting instead", RINGS, RINGS_SECONDS)
            return await self._text(text)
        self._rung.append(now)
        outgoing = self._outgoing[""] = _Outgoing(now, [text])  # held under "" until Twilio gives the call an id
        url = self._url + "/twilio/voice"
        try:
            self._outgoing[
                await self._twilio.call(self.owner.id, url + "/answered", url + "/machine", url + "/ended")
            ] = outgoing
        except httpx.HTTPError:
            log.warning("Couldn't ring; texting instead", exc_info=True)
            await self._text(text)
        finally:
            del self._outgoing[""]

    async def _text(self, text: str) -> None:
        try:
            await self._twilio.send_sms(self.owner.id, text)
        except httpx.HTTPError:  # e.g. the owner texted STOP; nowhere left to say it but the panel's transcript
            log.exception("Couldn't text the owner: %s", text)


def _xml(twiml: str) -> Response:
    return Response(twiml, media_type="application/xml")
