import asyncio
import contextlib

import httpx
import pydantic_ai
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from agent import config, logs
from agent.channels.cli import CliChannel
from agent.channels.panel import PanelChannel
from agent.gateway import Gateway
from agent.orchestrator import Orchestrator
from agent.phone.guard import ModelGuard
from agent.phone.investigator import ModelInvestigator
from agent.phone.pilot import Pilot
from agent.phone.relay import Phone
from agent.phone.vision import FallbackLocator, LlmLocator, create_locator
from agent.prompts import Prompts
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
    store = Store()
    store.interrupt_running()
    prompts = Prompts(store)
    locator = FallbackLocator(
        create_locator(config.vision_model(), api_key), LlmLocator(fallback, prompts.live("locator"))
    )
    pilot = Pilot(
        model,
        Phone(relay, config.device()),
        locator,
        ModelGuard(model, prompts.live("guard")),
        ModelInvestigator(fallback, prompts.live("investigator")),
        prompts.live("pilot"),
    )
    tasks = Tasks(store, pilot)
    orchestrator = Orchestrator(model, store, tasks, prompts.live("orchestrator"))
    gateway = Gateway([CliChannel(), PanelChannel(store, tasks, prompts)], orchestrator)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(gateway.run())
    print()


main()
