import asyncio
import contextlib
import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent import config
from agent.brain import Brain
from agent.channels.cli import CliChannel
from agent.gateway import Gateway
from agent.threads import Threads


def main() -> None:
    model = OpenRouterModel(config.model(), provider=OpenRouterProvider(api_key=config.openrouter_api_key()))
    gateway = Gateway([CliChannel()], Brain(model, Threads()))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(gateway.run())
    print()


main()
