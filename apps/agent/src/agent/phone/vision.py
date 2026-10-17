"""Finding things on the screen. Positions are fractions of the screenshot, from the top-left."""

import base64
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import activity, config, logs
from agent.messages import Image
from agent.models import Instructions, content, openrouter, short_name

log = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
VISIBLE_THRESHOLD = 0.5
INSTRUCTIONS = """\
You locate UI elements in phone screenshots. Return the tightest box around the described element as fractions \
(0-1) of the image's width and height, measured from the top-left. If it isn't on the screen, return found=false.\
"""


@dataclass(frozen=True)
class Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.right) / 2, (self.top + self.bottom) / 2


class Vision(Protocol):
    async def find(self, screen: Image, target: str) -> Box | None:
        """The box around `target`, or None if it isn't on the screen."""


def create_vision(model: str, instructions: Instructions = INSTRUCTIONS) -> Vision:
    """Jev for TypeSafe models (`instructions` don't apply); any other vision model is asked for a box."""
    if model.startswith("typesafe/"):
        key = config.openrouter_api_key()
        return JevVision(httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30), model)
    return ModelVision(openrouter(model), instructions)


class JevVision:
    """TypeSafe Jev: a Noul for "is it there?" and a Box for "where?", in one request."""

    def __init__(self, client: httpx.AsyncClient, model: str):
        self._client = client
        self._model = model

    async def find(self, screen: Image, target: str) -> Box | None:
        image = f"data:{screen.media_type};base64," + base64.b64encode(screen.data).decode()
        with activity.call(short_name(self._model), "model", f"find {target}") as reply:
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
            reply(f"visible {answers['visible']['noul']:.2f}")
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


class ModelVision:
    """Any vision model, asked for a bounding box as structured output."""

    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS):
        self._agent = Agent(model, name="vision", output_type=Located, instructions=instructions)

    async def find(self, screen: Image, target: str) -> Box | None:
        located = (await logs.run(self._agent, [f"Find: {target}", content(screen)])).output
        return Box(located.left, located.top, located.right, located.bottom) if located.found else None


class FallbackVision:
    """The primary vision model first; when it can't find the target or fails, a second model has a look.

    A miss costs the pilot a step and often sends it hunting for another route, so a second look is cheap.
    """

    def __init__(self, primary: Vision, fallback: Vision):
        self._primary = primary
        self._fallback = fallback

    async def find(self, screen: Image, target: str) -> Box | None:
        try:
            if box := await self._primary.find(screen, target):
                return box
            log.info("Couldn't find %s; asking the fallback vision model", target)
        except Exception as error:
            log.warning("Vision failed (%s); asking the fallback vision model", error)
        box = await self._fallback.find(screen, target)
        log.debug("fallback %r: %s", target, box)
        return box
