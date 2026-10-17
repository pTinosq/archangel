import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai import ModelMessage

from agent import config
from agent.messages import Conversation


@dataclass(frozen=True)
class _Thread:
    messages: list[ModelMessage]
    updated: float


class Threads:
    """Model history per conversation. A conversation left idle longer than `ttl` starts over."""

    def __init__(self, ttl: float = config.THREAD_TTL_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._threads: dict[Conversation, _Thread] = {}

    def history(self, conversation: Conversation) -> list[ModelMessage]:
        thread = self._threads.get(conversation)
        if not thread or self._clock() - thread.updated > self._ttl:
            self._threads.pop(conversation, None)
            return []
        return thread.messages

    def save(self, conversation: Conversation, messages: list[ModelMessage]) -> None:
        self._threads[conversation] = _Thread(messages, self._clock())
