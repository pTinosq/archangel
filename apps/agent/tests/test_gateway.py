import asyncio

from fakes import FakeChannel

from agent.events import OwnerMessage
from agent.gateway import Gateway
from agent.messages import Address, Message, Text


def test_every_channel_feeds_the_one_handler_and_the_transcript(store):
    cli, panel = FakeChannel("cli", ["hi"]), FakeChannel("panel", ["hello"])
    events = []

    async def handle(event):
        events.append(event)

    asyncio.run(Gateway([cli, panel], store).run(handle))

    assert {(e.reply_to.channel, e.message.text) for e in events} == {("cli", "hi"), ("panel", "hello")}
    assert all(isinstance(e, OwnerMessage) and e.from_owner for e in events)
    assert sorted(line.text for line in store.lines()) == ["hello", "hi"]


def test_messages_go_to_their_channel(store):
    cli, panel = FakeChannel("cli"), FakeChannel("panel")
    gateway = Gateway([cli, panel], store)

    asyncio.run(gateway.send(Message(panel.owner, (Text("done"),))))

    assert (cli.sent, [m.text for m in panel.sent]) == ([], ["done"])
    assert [(line.channel, line.sender) for line in store.lines()] == [("panel", "archangel")]
    assert gateway.owner("panel") == panel.owner
    assert gateway.owner("fax") is None


def test_a_failing_channel_doesnt_break_sending(store):
    class Broken(FakeChannel):
        async def send(self, message):
            raise ConnectionError("down")

    gateway = Gateway([Broken("sms")], store)
    asyncio.run(gateway.send(Message(Address("sms", "owner"), (Text("hi"),))))
    asyncio.run(gateway.send(Message(Address("fax", "owner"), (Text("hi"),))))
