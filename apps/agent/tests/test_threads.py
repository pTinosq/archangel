from pydantic_ai import ModelRequest

from agent.messages import Conversation
from agent.threads import Threads

CONVERSATION = Conversation("cli", "local")
HISTORY = [ModelRequest.user_text_prompt("hi")]


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def test_history_survives_within_ttl():
    clock = Clock()
    threads = Threads(ttl=60, clock=clock)
    threads.save(CONVERSATION, HISTORY)
    clock.now = 60
    assert threads.history(CONVERSATION) == HISTORY


def test_history_expires_after_ttl():
    clock = Clock()
    threads = Threads(ttl=60, clock=clock)
    threads.save(CONVERSATION, HISTORY)
    clock.now = 61
    assert threads.history(CONVERSATION) == []


def test_unknown_conversation_is_empty():
    assert Threads().history(CONVERSATION) == []
