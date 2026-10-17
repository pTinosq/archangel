import os

DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_VISION_MODEL = "typesafe/jev-1.14"
DEFAULT_RELAY_URL = "http://localhost:8080"
DEFAULT_DEVICE = "pixel-6"
THREAD_TTL_SECONDS = 2 * 60 * 60


def model() -> str:
    return os.environ.get("ARCHANGEL_MODEL", DEFAULT_MODEL)


def vision_model() -> str:
    return os.environ.get("ARCHANGEL_VISION_MODEL", DEFAULT_VISION_MODEL)


def relay_url() -> str:
    return os.environ.get("ARCHANGEL_RELAY_URL", DEFAULT_RELAY_URL)


def device() -> str:
    return os.environ.get("ARCHANGEL_DEVICE", DEFAULT_DEVICE)


def openrouter_api_key() -> str:
    return _required("OPENROUTER_API_KEY")


def relay_token() -> str:
    return _required("ARCHANGEL_TOKEN")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value
