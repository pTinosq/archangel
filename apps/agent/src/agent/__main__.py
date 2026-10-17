import asyncio
import contextlib
import sys

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
from agent.phone.vision import FallbackVision, ModelVision, create_vision
from agent.prompts import Prompts
from agent.store import Store
from agent.tasks import Tasks
from agent.web import Web


def main() -> None:
    pydantic_ai.BANNER_ENABLED = False
    logs.start()
    model, fallback = openrouter(config.model()), openrouter(config.fallback_vision_model())
    store = Store()
    store.interrupt_running()
    prompts = Prompts(store)

    relay = httpx.AsyncClient(
        base_url=config.relay_url(), headers={"Authorization": f"Bearer {config.relay_token()}"}, timeout=30
    )
    kit = Kit(
        phone=Phone(relay, config.device()),
        vision=FallbackVision(
            create_vision(config.vision_model(), prompts.live("vision")),
            ModelVision(fallback, prompts.live("vision")),
        ),
        guard=ModelGuard(model, prompts.live("guard")),
        investigator=ModelInvestigator(fallback, prompts.live("investigator")),
    )
    tasks = Tasks(store, Pilot(model, kit, prompts.live("pilot")))

    web = Web(config.github(), config.port())
    channels = [PanelChannel(store, prompts, web)]
    if sys.stdin.isatty():  # not when deployed
        channels.insert(0, CliChannel())
    gateway = Gateway(channels, store)
    orchestrator = Orchestrator(model, store, tasks, gateway, prompts.live("orchestrator"))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(web, gateway, orchestrator))
    print()


async def _run(web: Web, gateway: Gateway, orchestrator: Orchestrator) -> None:
    async with asyncio.TaskGroup() as group:
        group.create_task(web.serve())
        group.create_task(gateway.run(orchestrator.handle))


main()
