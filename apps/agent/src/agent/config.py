import os

DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_VISION_MODEL = "typesafe/jev-1.14"
DEFAULT_FALLBACK_VISION_MODEL = "openai/gpt-6-luna"
DEFAULT_RELAY_URL = "http://localhost:8080"


def model() -> str:
    return os.environ.get("ARCHANGEL_MODEL", DEFAULT_MODEL)


def vision_model() -> str:
    return os.environ.get("ARCHANGEL_VISION_MODEL", DEFAULT_VISION_MODEL)


def fallback_vision_model() -> str:
    return os.environ.get("ARCHANGEL_FALLBACK_VISION_MODEL", DEFAULT_FALLBACK_VISION_MODEL)


def relay_url() -> str:
    return os.environ.get("ARCHANGEL_RELAY_URL", DEFAULT_RELAY_URL)


def device() -> str | None:
    """None: whichever phone is connected, if only one is."""
    return os.environ.get("ARCHANGEL_DEVICE") or None


def openrouter_api_key() -> str:
    return _required("OPENROUTER_API_KEY")


def relay_token() -> str:
    return _required("ARCHANGEL_TOKEN")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value
