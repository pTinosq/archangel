import asyncio
from urllib.parse import parse_qs

import httpx

from agent.channels.twilio import MORE, SMS_LIMIT, Account, SmsChannel, Twilio, signature
from agent.messages import Address, Image, Message, Text
from agent.web import Web

ACCOUNT = Account("AC1", "token", "+447700900999", "+447700900123")
URL = "http://localhost/twilio/sms"


def test_signature_matches_twilios():
    # Computed with Twilio's own RequestValidator (twilio-python).
    form = {"From": "+447700900123", "Body": "hi there", "To": "+447700900999", "MessageSid": "SM1"}
    assert signature("tok", "https://agent.example/twilio/sms", form) == "YYe+mw/1Mc9ndNGpjrtjoqk5k7g="


class Sms:
    """An SmsChannel on a local web server, with Twilio's API faked."""

    def __init__(self, panel_url: str | None = "https://archangel.example"):
        self.sent: list[dict[str, list[str]]] = []
        self.delivered: list[Message] = []

        def api(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2010-04-01/Accounts/AC1/Messages.json"
            self.sent.append(parse_qs(request.content.decode()))
            return httpx.Response(201, json={})

        self.web = Web()
        twilio = Twilio(ACCOUNT, httpx.AsyncClient(transport=httpx.MockTransport(api)))
        self.channel = SmsChannel(twilio, self.web, panel_url)

        async def deliver(message):
            self.delivered.append(message)

        self.channel._deliver = deliver

    def text(self, sender: str, body: str, forged: bool = False) -> httpx.Response:
        form = {"From": sender, "To": ACCOUNT.number, "Body": body}
        sig = signature("not the token" if forged else ACCOUNT.token, URL, form)

        async def go():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(self.web.finish()), base_url="http://localhost"
            ) as c:
                return await c.post("/twilio/sms", data=form, headers={"X-Twilio-Signature": sig})

        return asyncio.run(go())


def test_the_owners_texts_join_the_conversation():
    sms = Sms()
    response = sms.text(ACCOUNT.owner, " open whatsapp ")
    assert response.status_code == 200 and response.text == "<Response/>"
    assert sms.delivered == [Message(Address("sms", ACCOUNT.owner), (Text("open whatsapp"),))]


def test_unsigned_texts_are_refused_and_strangers_ignored():
    sms = Sms()
    assert sms.text(ACCOUNT.owner, "approve", forged=True).status_code == 403
    assert sms.text("+447700900666", "approve").status_code == 200
    assert sms.delivered == []


def test_replies_go_through_twilio_capped_with_a_link_for_screenshots():
    sms = Sms()
    screen = Image(b"jpeg", "image/jpeg")
    asyncio.run(sms.channel.send(Message(Address("sms", ACCOUNT.owner), (Text("Post this?"), screen))))
    asyncio.run(sms.channel.send(Message(Address("sms", ACCOUNT.owner), (Text("x" * 2000),))))

    first, long = sms.sent
    assert first == {
        "To": [ACCOUNT.owner],
        "From": [ACCOUNT.number],
        "Body": ["Post this?\n\nScreenshot in the panel: https://archangel.example"],
    }
    assert len(long["Body"][0]) == SMS_LIMIT and long["Body"][0].endswith(MORE)
