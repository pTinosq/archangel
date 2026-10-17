import asyncio
import contextlib

import httpx
import pydantic_ai

from agent import config, logs
from agent.channels.cli import CliChannel
from agent.channels.panel import PanelChannel
from agent.gateway import Gateway
from agent.models import openrouter
from agent.orchestrator import Orchestrator
from agent.phone.guard import ModelGuard
from agent.phone.investigator import ModelInvestigator
from agent.phone.pilot import Kit, Pilot
from agent.phone.relay import Phone
from agent.phone.vision import FallbackLocator, LlmLocator, create_locator
from agent.prompts import Prompts
from agent.store import Store
from agent.tasks import Tasks


def main() -> None:
    pydantic_ai.BANNER_ENABLED = False
    logs.start()
    model, fallback = openrouter(config.model()), openrouter(config.fallback_model())
    store = Store()
    store.interrupt_running()
    prompts = Prompts(store)

    relay = httpx.AsyncClient(
        base_url=config.relay_url(), headers={"Authorization": f"Bearer {config.relay_token()}"}, timeout=30
    )
    kit = Kit(
        phone=Phone(relay, config.device()),
        locator=FallbackLocator(
            create_locator(config.vision_model(), prompts.live("locator")),
            LlmLocator(fallback, prompts.live("locator")),
        ),
        guard=ModelGuard(model, prompts.live("guard")),
        investigator=ModelInvestigator(fallback, prompts.live("investigator")),
    )
    tasks = Tasks(store, Pilot(model, kit, prompts.live("pilot")))

    gateway = Gateway([CliChannel(), PanelChannel(store, prompts)], store)
    orchestrator = Orchestrator(model, store, tasks, gateway, prompts.live("orchestrator"))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(gateway.run(orchestrator.handle))
    print()


main()
