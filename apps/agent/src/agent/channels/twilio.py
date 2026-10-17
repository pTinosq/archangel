"""Twilio: the owner texts (and later calls) Archangel's number.

Twilio's webhooks skip GitHub sign-in (`login.OPEN`); each one is checked against Twilio's signature instead, and
anything not from the owner's number is ignored. Twilio's REST API is a couple of form posts, so no SDK.
"""

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from urllib.parse import parse_qsl

import httpx
from fastapi import Request, Response

from agent.channels import Channel, Deliver
from agent.messages import Address, Message, Text
from agent.web import Web

log = logging.getLogger(__name__)

API = "https://api.twilio.com/2010-04-01"
SMS_LIMIT = 1600  # Twilio's cap for one message, however many parts it's sent in
MORE = "… (more in the panel)"
EMPTY = "<Response/>"  # TwiML for "nothing to do": replies are sent separately


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

    async def _post(self, path: str, form: dict[str, str]) -> None:
        url = f"{API}/Accounts/{self.account.sid}/{path}"
        response = await self._client.post(url, data=form, auth=(self.account.sid, self.account.token))
        response.raise_for_status()

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

    def __init__(self, twilio: Twilio, web: Web, panel_url: str | None):
        self.owner = Address(self.name, twilio.account.owner)
        self._twilio = twilio
        self._web = web
        self._panel_url = panel_url
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
            return Response(EMPTY, media_type="application/xml")

    async def start(self, deliver: Deliver) -> None:
        self._deliver = deliver
        await self._web.closed()

    async def send(self, message: Message) -> None:
        text = message.text
        if message.images:
            text += (
                f"\n\nScreenshot in the panel: {self._panel_url}" if self._panel_url else "\n\nScreenshot in the panel."
            )
        if len(text) > SMS_LIMIT:
            text = text[: SMS_LIMIT - len(MORE)] + MORE
        await self._twilio.send_sms(message.address.id, text)
