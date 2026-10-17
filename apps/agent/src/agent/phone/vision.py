"""Finding things on the screen. Positions are fractions of the screenshot, from the top-left."""

import base64
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent.phone.relay import Screenshot

log = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
VISIBLE_THRESHOLD = 0.5


@dataclass(frozen=True)
class Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.right) / 2, (self.top + self.bottom) / 2


class Locator(Protocol):
    async def locate(self, screenshot: Screenshot, target: str) -> Box | None:
        """The box around `target`, or None if it isn't on the screen."""


def create_locator(model: str, api_key: str) -> Locator:
    if model.startswith("typesafe/"):
        client = httpx.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        return JevLocator(client, model)
    return LlmLocator(OpenRouterModel(model, provider=OpenRouterProvider(api_key=api_key)))


class JevLocator:
    """TypeSafe Jev: a Noul for "is it there?" and a Box for "where?", in one request."""

    def __init__(self, client: httpx.AsyncClient, model: str):
        self._client = client
        self._model = model

    async def locate(self, screenshot: Screenshot, target: str) -> Box | None:
        image = "data:image/jpeg;base64," + base64.b64encode(screenshot.jpeg).decode()
        response = await self._client.post(
            DECISIONS_URL,
            headers={"TypeSafe-Danger-Zone-Images": "true"},
            json={
                "model": self._model,
                "state": {"screen": {"type": "image_url", "image_url": {"url": image}}},
                "questions": {
                    "visible": {"type": "noul", "instructions": f"Is {target} visible in `screen`?"},
                    "box": {"type": "box", "instructions": f"{target} in `screen`"},
                },
            },
        )
        response.raise_for_status()
        answers = response.json()["answers"]
        log.debug("jev %r: %s", target, answers)
        if answers["visible"]["noul"] < VISIBLE_THRESHOLD:
            return None
        return Box(**answers["box"]["box"])


@dataclass
class Located:
    found: bool
    left: float = 0
    top: float = 0
    right: float = 0
    bottom: float = 0


class LlmLocator:
    """Any vision model, asked for a bounding box as structured output."""

    def __init__(self, model: OpenRouterModel):
        self._agent = Agent(
            model,
            output_type=Located,
            instructions=(
                "You locate UI elements in phone screenshots. Return the tightest box around the described "
                "element as fractions (0-1) of the image's width and height, measured from the top-left. "
                "If it isn't on the screen, return found=false."
            ),
        )

    async def locate(self, screenshot: Screenshot, target: str) -> Box | None:
        image = BinaryContent(data=screenshot.jpeg, media_type="image/jpeg")
        located = (await self._agent.run([f"Find: {target}", image])).output
        return Box(located.left, located.top, located.right, located.bottom) if located.found else None
