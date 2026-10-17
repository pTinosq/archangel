import asyncio

from agent.channels import Channel, Deliver
from agent.gateway import Gateway, Reply
from agent.messages import Conversation, Message, Text

A = Conversation("fake", "a")
B = Conversation("fake", "b")


def message(conversation: Conversation, text: str) -> Message:
    return Message(conversation, (Text(text),))


class FakeChannel(Channel):
    name = "fake"

    def __init__(self, inbound: list[Message]):
        self.inbound = inbound
        self.sent: list[Message] = []

    async def start(self, deliver: Deliver) -> None:
        for item in self.inbound:
            await deliver(item)

    async def send(self, message: Message) -> None:
        self.sent.append(message)


class EchoBrain:
    """Replies twice; the first message in each conversation is slow."""

    def __init__(self):
        self.seen: set[Conversation] = set()

    async def handle(self, message: Message, reply: Reply) -> None:
        if message.conversation not in self.seen:
            self.seen.add(message.conversation)
            await asyncio.sleep(0.05)
        await reply(message)
        await reply(message)


def run(inbound: list[Message], brain) -> list[str]:
    channel = FakeChannel(inbound)
    asyncio.run(Gateway([channel], brain).run())
    return [f"{sent.conversation.id}:{sent.text}" for sent in channel.sent]


def test_replies_go_back_to_the_conversation_in_order():
    sent = run([message(A, "1"), message(A, "2")], EchoBrain())
    assert sent == ["a:1", "a:1", "a:2", "a:2"]


def test_conversations_do_not_wait_on_each_other():
    class SlowA(EchoBrain):
        async def handle(self, message, reply):
            if message.conversation == A:
                await asyncio.sleep(0.05)
            await reply(message)

    assert run([message(A, "slow"), message(B, "fast")], SlowA()) == ["b:fast", "a:slow"]


def test_brain_failure_is_reported_to_the_conversation():
    class Broken:
        async def handle(self, message, reply):
            raise RuntimeError("boom")

    assert run([message(A, "hi")], Broken()) == ["a:Something went wrong (RuntimeError). Try again."]
