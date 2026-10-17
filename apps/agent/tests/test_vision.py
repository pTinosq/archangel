import asyncio

import httpx

from agent.messages import Image
from agent.phone.vision import Box, FallbackVision

SCREEN = Image(b"jpg", "image/jpeg")
BOX = Box(0.1, 0.1, 0.2, 0.2)


class FakeVision:
    def __init__(self, answer: Box | None | Exception):
        self.answer = answer
        self.asked = 0

    async def find(self, screen: Image, target: str) -> Box | None:
        self.asked += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def find(primary: FakeVision, fallback: FakeVision) -> Box | None:
    return asyncio.run(FallbackVision(primary, fallback).find(SCREEN, "the Send button"))


def test_the_fallback_is_not_asked_when_the_primary_finds_it():
    fallback = FakeVision(None)
    assert find(FakeVision(BOX), fallback) == BOX
    assert fallback.asked == 0


def test_the_fallback_looks_when_the_primary_misses():
    assert find(FakeVision(None), FakeVision(BOX)) == BOX


def test_the_fallback_looks_when_the_primary_fails():
    assert find(FakeVision(httpx.ConnectError("down")), FakeVision(BOX)) == BOX
