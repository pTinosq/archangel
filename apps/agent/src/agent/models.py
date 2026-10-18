"""Where Archangel meets the model library: building models, and turning our types into model input."""

from collections.abc import Callable, Sequence

from pydantic_ai import BinaryContent, UserContent
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent import config
from agent.messages import Image, Part, Text

Instructions = str | Callable[[], str]
"""A fixed prompt, or one read at the start of each run so edits apply without a restart."""


TIMEOUT = 120
"""Seconds for one model call. A stalled one otherwise waits the client's 10 minutes (then is retried twice), and
the orchestrator answers one message at a time: everything the owner says waits behind it."""


TRUSTED = [
    *("digitalocean", "deepinfra", "wafer", "parasail", "fireworks", "together", "baseten", "novita"),  # open models
    *("anthropic", "openai", "perplexity", "typesafe"),  # makers
    *("amazon-bedrock", "google-vertex", "azure", "claude-on-aws"),  # clouds serving makers' models
]
"""The only hosts OpenRouter may send a call to: the first that serves the model, then, if it fails, the next (the
owner's order). An open model like DeepSeek has dozens, some running cut-down copies that answer gibberish (Morph's
did, for minutes). A new model may need its host added here."""


def openrouter(name: str, timeout: float = TIMEOUT) -> Model:
    """Every model goes through OpenRouter, so any of its models works by name, on a `TRUSTED` host. A call taking
    over `timeout` seconds is abandoned and retried."""
    provider = OpenRouterProvider(api_key=config.openrouter_api_key())
    settings = OpenRouterModelSettings(timeout=timeout, openrouter_provider={"order": TRUSTED, "only": TRUSTED})
    return OpenRouterModel(name, provider=provider, settings=settings)


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
