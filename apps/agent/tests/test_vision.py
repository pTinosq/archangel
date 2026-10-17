import asyncio

import httpx

from agent.phone.relay import Screenshot
from agent.phone.vision import Box, FallbackLocator

SCREEN = Screenshot(b"jpg", 540, 1200)
BOX = Box(0.1, 0.1, 0.2, 0.2)


class FakeLocator:
    def __init__(self, answer: Box | None | Exception):
        self.answer = answer
        self.asked = 0

    async def locate(self, screenshot: Screenshot, target: str) -> Box | None:
        self.asked += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def locate(primary: FakeLocator, fallback: FakeLocator) -> Box | None:
    return asyncio.run(FallbackLocator(primary, fallback).locate(SCREEN, "the Send button"))


def test_the_fallback_is_not_asked_when_the_primary_finds_it():
    fallback = FakeLocator(None)
    assert locate(FakeLocator(BOX), fallback) == BOX
    assert fallback.asked == 0


def test_the_fallback_looks_when_the_primary_misses():
    assert locate(FakeLocator(None), FakeLocator(BOX)) == BOX


def test_the_fallback_looks_when_the_primary_fails():
    assert locate(FakeLocator(httpx.ConnectError("down")), FakeLocator(BOX)) == BOX
