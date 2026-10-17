import asyncio
import json
from urllib.parse import parse_qs
from xml.etree import ElementTree

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent.channels.twilio import MORE, SMS_LIMIT, Account, SmsChannel, Twilio, VoiceChannel, signature
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


RELAY = "wss://archangel.example/twilio/voice/relay"


class Voice:
    """A VoiceChannel on a local web server, with Twilio's API faked."""

    def __init__(self):
        self.calls: list[dict[str, list[str]]] = []
        self.texts: list[dict[str, list[str]]] = []
        self.delivered: list[Message] = []

        def api(request: httpx.Request) -> httpx.Response:
            kind = request.url.path.removeprefix("/2010-04-01/Accounts/AC1/")
            (self.calls if kind == "Calls.json" else self.texts).append(parse_qs(request.content.decode()))
            return httpx.Response(201, json={})

        web = Web()
        twilio = Twilio(ACCOUNT, httpx.AsyncClient(transport=httpx.MockTransport(api)))
        self.channel = VoiceChannel(twilio, web, "https://archangel.example")
        self.client = TestClient(web.finish(), base_url="http://localhost")

        async def deliver(message):
            self.delivered.append(message)

        self.channel._deliver = deliver

    def ring(self, caller: str, forged: bool = False) -> httpx.Response:
        form = {"From": caller, "To": ACCOUNT.number, "CallSid": "CA1"}
        sig = signature("not the token" if forged else ACCOUNT.token, "http://localhost/twilio/voice", form)
        return self.client.post("/twilio/voice", data=form, headers={"X-Twilio-Signature": sig})

    def relay(self, forged: bool = False):
        sig = signature("not the token" if forged else ACCOUNT.token, RELAY, {})
        return self.client.websocket_connect("ws://localhost/twilio/voice/relay", headers={"X-Twilio-Signature": sig})


def test_the_owners_call_is_answered_and_strangers_rejected():
    voice = Voice()
    assert voice.ring(ACCOUNT.owner, forged=True).status_code == 403
    assert voice.ring("+447700900666").text == "<Response><Reject/></Response>"
    answered = voice.ring(ACCOUNT.owner).text
    assert f'<ConversationRelay url="{RELAY}"' in answered and 'welcomeGreeting="Archangel."' in answered


def test_only_twilio_can_open_the_relay():
    with pytest.raises(WebSocketDisconnect), Voice().relay(forged=True) as ws:
        ws.receive_text()


def test_what_the_owner_says_joins_the_conversation_and_the_reply_is_spoken():
    voice = Voice()
    with voice.relay() as ws:
        ws.send_json({"type": "setup", "callSid": "CA1", "from": ACCOUNT.owner})
        ws.send_json({"type": "prompt", "voicePrompt": "what's my battery?", "last": True})
        ws.send_json({"type": "interrupt"})  # ignored, but proves the prompt was handled first
        asyncio.run(voice.channel.send(Message(Address("voice", "CA1"), (Text("Eighty percent."),))))
        assert json.loads(ws.receive_text()) == {"type": "text", "token": "Eighty percent.", "last": True}
    assert voice.delivered == [Message(Address("voice", "CA1"), (Text("what's my battery?"),))]
    assert voice.calls == voice.texts == []


def test_while_on_a_call_everything_is_said_on_it():
    voice = Voice()
    with voice.relay() as ws:
        ws.send_json({"type": "setup", "callSid": "CA2"})
        ws.send_json({"type": "interrupt"})  # setup handled
        asyncio.run(voice.channel.send(Message(voice.channel.owner, (Text("Done."),))))
        assert json.loads(ws.receive_text())["token"] == "Done."
    assert voice.calls == voice.texts == []


def test_after_a_hang_up_it_texts_rather_than_ringing():
    voice = Voice()
    asyncio.run(voice.channel.send(Message(Address("voice", "CA1"), (Text("Flashlight's on."),))))
    assert voice.calls == []
    assert voice.texts == [{"To": [ACCOUNT.owner], "From": [ACCOUNT.number], "Body": ["Flashlight's on."]}]


def test_asked_for_a_call_back_it_rings_the_owner_and_says_it():
    voice = Voice()
    asyncio.run(voice.channel.send(Message(voice.channel.owner, (Text('Ready to send "hi" & go?'),))))
    (call,) = voice.calls
    assert call["To"] == [ACCOUNT.owner] and call["From"] == [ACCOUNT.number]
    relay = ElementTree.fromstring(call["Twiml"][0]).find("Connect/ConversationRelay")
    assert relay.get("welcomeGreeting") == 'Ready to send "hi" & go?' and relay.get("url") == RELAY
