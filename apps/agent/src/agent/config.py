import os

DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
THREAD_TTL_SECONDS = 2 * 60 * 60


def model() -> str:
    return os.environ.get("ARCHANGEL_MODEL", DEFAULT_MODEL)


def openrouter_api_key() -> str:
    value = os.environ.get("OPENROUTER_API_KEY")
    if not value:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return value
