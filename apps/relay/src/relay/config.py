import os

COMMAND_TIMEOUT = 15.0
DEFAULT_PORT = 8080


def token() -> str:
    value = os.environ.get("ARCHANGEL_TOKEN")
    if not value:
        raise RuntimeError("ARCHANGEL_TOKEN is not set")
    return value


def port() -> int:
    return int(os.environ.get("PORT", DEFAULT_PORT))
