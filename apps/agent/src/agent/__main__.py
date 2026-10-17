import asyncio
import contextlib

import httpx
import pydantic_ai
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent import config, logs
from agent.channels.cli import CliChannel
from agent.gateway import Gateway
from agent.orchestrator import Orchestrator
from agent.phone.guard import ModelGuard
from agent.phone.investigator import ModelInvestigator
from agent.phone.pilot import Pilot
from agent.phone.relay import Phone
from agent.phone.vision import FallbackLocator, LlmLocator, create_locator
from agent.store import Store
from agent.tasks import Tasks


def main() -> None:
    pydantic_ai.BANNER_ENABLED = False
    logs.start()
    api_key = config.openrouter_api_key()
    provider = OpenRouterProvider(api_key=api_key)
    model = OpenRouterModel(config.model(), provider=provider)
    fallback = OpenRouterModel(config.fallback_model(), provider=provider)
    relay = httpx.AsyncClient(
        base_url=config.relay_url(),
        headers={"Authorization": f"Bearer {config.relay_token()}"},
        timeout=30,
    )
    locator = FallbackLocator(create_locator(config.vision_model(), api_key), LlmLocator(fallback))
    pilot = Pilot(model, Phone(relay, config.device()), locator, ModelGuard(model), ModelInvestigator(fallback))
    store = Store()
    store.interrupt_running()
    gateway = Gateway([CliChannel()], Orchestrator(model, store, Tasks(store, pilot)))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(gateway.run())
    print()


main()
