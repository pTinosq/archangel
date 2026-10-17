import asyncio
import contextlib

import httpx
import pydantic_ai
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent import config, logs
from agent.brain import Brain
from agent.channels.cli import CliChannel
from agent.gateway import Gateway
from agent.phone.guard import ModelGuard
from agent.phone.pilot import Pilot
from agent.phone.relay import Phone
from agent.phone.vision import create_locator
from agent.store import Store


def main() -> None:
    pydantic_ai.BANNER_ENABLED = False
    logs.start()
    api_key = config.openrouter_api_key()
    model = OpenRouterModel(config.model(), provider=OpenRouterProvider(api_key=api_key))
    relay = httpx.AsyncClient(
        base_url=config.relay_url(),
        headers={"Authorization": f"Bearer {config.relay_token()}"},
        timeout=30,
    )
    pilot = Pilot(model, Phone(relay, config.device()), create_locator(config.vision_model(), api_key), ModelGuard(model))
    gateway = Gateway([CliChannel()], Brain(model, Store(), pilot))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(gateway.run())
    print()


main()
