"""Twilio: the owner texts and calls Archangel's number, and it texts and rings them back.

Twilio's webhooks skip GitHub sign-in (`login.OPEN`); each one is checked against Twilio's signature instead, and
anything not from the owner's number is ignored. Twilio's REST API is a couple of form posts, so no SDK.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape, quoteattr

import httpx
from fastapi import Request, Response, WebSocket, WebSocketDisconnect

from agent.channels import Channel, Deliver
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
MISSED = "Tried to call you. "
GREETING = "Archangel."
RING_SECONDS = 120  # a call Archangel makes is answered or given up on well within this
RINGS = 3  # at most this many calls Archangel makes…
RINGS_SECONDS = 600  # …in this long; beyond that, it texts


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

    async def call(self, to: str, answered: str, ended: str) -> str:
        """Ring `to`; Twilio asks `answered` what to do once it knows who picked up, and tells `ended` how it went.
        Returns the call's id."""
        # DetectMessageEnd: on a voicemail, Twilio waits for the beep before asking, so a message can be left.
        form = {"To": to, "From": self.account.number, "Url": answered, "MachineDetection": "DetectMessageEnd"}
        return (await self._post("Calls.json", form | {"StatusCallback": ended}))["sid"]

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


class VoiceChannel(Channel):
    """Phone calls with the owner. Twilio's ConversationRelay does the listening and speaking; this trades text with it
    over a WebSocket.

    While the owner's on a call, everything is said on it. Otherwise Archangel rings only if they asked for a call back
    (the message is addressed to them, not to a call), a few times in ten minutes at most. A voicemail gets the message
    after the beep, then a hang-up, so a recording is never taken for the owner; voicemails and missed calls get a text
    too. News for a call that has ended is texted, but replies to what was said on it aren't: hanging up after "bye"
    shouldn't bring a text saying "Goodbye."
    """

    name = "voice"
    shows = "speech; screenshots are texted as a link"

    def __init__(self, twilio: Twilio, web: Web, public_url: str, links: Links):
        self.owner = Address(self.name, twilio.account.owner)
        self._twilio = twilio
        self._web = web
        self._url = public_url
        self._links = links
        # The URL Twilio signs the handshake with is the one we give it; behind a proxy, request.url isn't that.
        self._relay = public_url.replace("https://", "wss://", 1) + "/twilio/voice/relay"
        self._calls: dict[str, WebSocket] = {}  # live, by call id
        # Calls Archangel is making, from ringing until they're live: when each started, and what's still to say.
        self._ringing: dict[str, tuple[float, list[str]]] = {}
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
            call = form.get("CallSid", "")
            said = " ".join(self._ringing.pop(call, (0, []))[1]) or GREETING
            if form.get("AnsweredBy", "unknown") in ("human", "unknown"):
                self._ringing[call] = (time.monotonic(), [])  # until it's live, more is said once it is
                return _xml(self._twiml(said))
            log.info("Voicemail picked up; leaving a message and texting too")
            await self._text(MISSED + said)
            if form.get("AnsweredBy", "").startswith("machine_end"):  # after the beep
                return _xml(f'<Response><Say language="en-GB">{escape(said)}</Say><Hangup/></Response>')
            return _xml(HANG_UP)  # a fax, or a machine it couldn't wait out

        @web.app.post("/twilio/voice/ended")
        async def ended(request: Request) -> Response:
            if (form := await twilio.signed(request)) is None:
                return Response(status_code=403)
            if left := self._ringing.pop(form.get("CallSid", ""), (0, []))[1]:
                missed = form.get("CallStatus") in UNANSWERED
                log.info("Call %s with things unsaid; texting them", form.get("CallStatus"))
                await self._text((MISSED if missed else "") + " ".join(left))
            return _xml(EMPTY)

        @web.app.websocket("/twilio/voice/relay")
        async def relay(websocket: WebSocket) -> None:
            expected = signature(twilio.account.token, self._relay, {})
            if not hmac.compare_digest(expected, websocket.headers.get("x-twilio-signature", "")):
                await websocket.close(code=1008)
                return
            await websocket.accept()
            call = ""
            try:
                while True:
                    event = await websocket.receive_json()
                    if event.get("type") == "setup":
                        call = event.get("callSid", "")
                        self._calls[call] = websocket
                        for text in self._ringing.pop(call, (0, []))[1]:  # what came up while it connected
                            await self._say(websocket, text)
                    elif event.get("type") == "prompt" and (said := event.get("voicePrompt", "").strip()):
                        if self._deliver:
                            await self._deliver(Message(Address(self.name, call), (Text(said),)))
                    elif event.get("type") == "error":
                        log.warning("Call %s: %s", call, event.get("description"))
            except WebSocketDisconnect:
                pass
            finally:
                self._calls.pop(call, None)

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
            return
        if to == self.owner.id:
            await self._ring(text)
        elif message.answering:
            log.info("Not texting a reply to a call that has ended: %s", text)
        else:
            await self._text(text)

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
        self._ringing = {call: entry for call, entry in self._ringing.items() if now - entry[0] < RING_SECONDS}
        if self._ringing:  # already ringing them: say it on that call too
            next(iter(self._ringing.values()))[1].append(text)
            return
        if len(self._rung) == RINGS and now - self._rung[0] < RINGS_SECONDS:
            log.warning("Rung %d times in %d s; texting instead", RINGS, RINGS_SECONDS)
            return await self._text(text)
        self._rung.append(now)
        said = [text]
        self._ringing[""] = (now, said)  # held under "" until Twilio gives the call an id
        url = self._url + "/twilio/voice"
        try:
            call = await self._twilio.call(self.owner.id, url + "/answered", url + "/ended")
            self._ringing[call] = (now, said)
        except httpx.HTTPError:
            log.warning("Couldn't ring; texting instead", exc_info=True)
            await self._text(" ".join(said))
        finally:
            del self._ringing[""]

    async def _text(self, text: str) -> None:
        try:
            await self._twilio.send_sms(self.owner.id, text)
        except httpx.HTTPError:  # e.g. the owner texted STOP; nowhere left to say it but the panel's transcript
            log.exception("Couldn't text the owner: %s", text)


def _xml(twiml: str) -> Response:
    return Response(twiml, media_type="application/xml")
