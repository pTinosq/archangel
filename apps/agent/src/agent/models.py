"""Where Archangel meets the model library: building models, and turning our types into model input."""

from collections.abc import Callable, Sequence

from pydantic_ai import BinaryContent, ModelSettings, UserContent
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent import config
from agent.messages import Image, Part, Text

Instructions = str | Callable[[], str]
"""A fixed prompt, or one read at the start of each run so edits apply without a restart."""


TIMEOUT = 120
"""Seconds for one model call. A stalled one otherwise waits the client's 10 minutes (then is retried twice), and
the orchestrator answers one message at a time: everything the owner says waits behind it."""


def openrouter(name: str, timeout: float = TIMEOUT) -> Model:
    """Every model goes through OpenRouter, so any of its models works by name. A call taking over `timeout` seconds
    is abandoned and retried."""
    provider = OpenRouterProvider(api_key=config.openrouter_api_key())
    return OpenRouterModel(name, provider=provider, settings=ModelSettings(timeout=timeout))


def content(image: Image) -> BinaryContent:
    return BinaryContent(data=image.data, media_type=image.media_type)


def prompt(parts: Sequence[Part | str]) -> list[UserContent]:
    return [part if isinstance(part, str) else part.text if isinstance(part, Text) else content(part) for part in parts]


def describe(parts: Sequence[Part | str]) -> str:
    """For logs: the text, with images as placeholders rather than bytes."""
    return " ".join(
        part if isinstance(part, str) else part.text if isinstance(part, Text) else "[image]" for part in parts
    )


def short_name(model: Model | str) -> str:
    """openai/gpt-6-luna → gpt-6-luna: how a model is named where it's shown, e.g. the cockpit."""
    return str(getattr(model, "model_name", model)).split("/")[-1]
