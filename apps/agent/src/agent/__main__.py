import asyncio
import contextlib
import sys

import httpx
import pydantic_ai

from agent import config, logs
from agent.channels.cli import CliChannel
from agent.channels.panel import PanelChannel
from agent.channels.twilio import SmsChannel, Twilio, VoiceChannel
from agent.coder import Coder, github
from agent.connectors import Connectors
from agent.events import Scheduled, TaskUpdate
from agent.gateway import Gateway
from agent.helper import Helper
from agent.links import Links
from agent.location import Whereabouts
from agent.models import openrouter
from agent.orchestrator import Orchestrator
from agent.devices.guard import ModelGuard
from agent.devices.investigator import ModelInvestigator
from agent.devices.pc import PC
from agent.devices.phone import PHONE
from agent.devices.pilot import Kit, Pilot
from agent.devices.relay import Device
from agent.devices.vision import FallbackVision, ModelVision, create_vision
from agent.prompts import Prompts
from agent.reports import Reporter
from agent.research import DEEP, DEEP_TIMEOUT, RESEARCH, Researcher, tavily
from agent.schedule import Schedule
from agent.store import DATA, Store, Task
from agent.tasks import Tasks
from agent.threads import Threads
from agent.threads.router import Router
from agent.threads.store import ThreadStore
from agent.threads.summariser import Summariser
from agent.tools import Tools
from agent.twin import Twin
from agent.twin.graph import Graph
from agent.twin.reader import Reader
from agent.twin.writer import Writer
from agent.web import Web


def main() -> None:
    pydantic_ai.BANNER_ENABLED = False
    logs.start()
    model, fallback = openrouter(config.model()), openrouter(config.fallback_vision_model())
    store = Store()
    interrupted = store.interrupt_running()
    prompts = Prompts(store)

    relay = httpx.AsyncClient(
        base_url=config.relay_url(), headers={"Authorization": f"Bearer {config.relay_token()}"}, timeout=30
    )
    phone = Device(relay, "android", config.devices(), store)
    devices = {"phone": phone, "pc": Device(relay, "windows", config.devices(), store, wake=phone.wake)}
    vision = FallbackVision(
        create_vision(config.vision_model(), prompts.live("vision")), ModelVision(fallback, prompts.live("vision"))
    )
    guard, investigator = (
        ModelGuard(model, prompts.live("guard")),
        ModelInvestigator(fallback, prompts.live("investigator")),
    )
    search = (key := config.tavily_api_key()) and tavily(key)
    researcher = Researcher(openrouter(RESEARCH), openrouter(DEEP, DEEP_TIMEOUT), search)
    site = config.public_url() or f"http://127.0.0.1:{config.port()}"
    connectors = Connectors(store, site, DATA)
    tools = Tools(store)
    # Everything the owner says waits behind the orchestrator, so it answers without reasoning first; the twin's
    # reader too, as every task waits for its briefing. The background agents (pilot, guard, ...) keep reasoning:
    # they find their way through screens and judge risk.
    quick = openrouter(config.model(), think=False)
    graph = Graph(store.db)
    thread_store = ThreadStore(store.db)
    threads = Threads(
        thread_store,
        Router(quick, thread_store, prompts.live("thread router")),  # every message waits for it, as for the reader
        Summariser(model, prompts.live("thread summariser")),
    )
    twin = Twin(
        store,
        graph,
        Reader(quick, graph, prompts.live("twin reader")),
        Writer(model, graph, prompts.live("twin writer")),
        lambda channel: gateway.owner(channel),  # the gateway comes later, with the channels
    )
    coder = Coder(github(token)) if (token := config.github_token()) else None
    pilots = {
        kind: Pilot(
            model,
            Kit(devices[kind], vision, guard, investigator),
            platform,
            prompts.live(f"{kind} pilot"),
            [twin.tools],
        )
        for kind, platform in (("phone", PHONE), ("pc", PC))
    }
    tasks = Tasks(
        store,
        pilots,
        researcher,
        Reporter(model, prompts.live("reporter")),
        Helper(model, connectors, prompts.live("helper"), [twin.tools]),
        site,
        twin.brief,
        coder,
    )

    web = Web(config.github(), config.port(), config.public_url())
    schedule = Schedule(store.db)
    channels = [PanelChannel(store, prompts, connectors, tools, graph, threads, schedule, web)]
    public_url = config.public_url()  # Twilio needs it to reach the calls; texts link screenshots and location with it
    links = Links(store, web, config.relay_token(), public_url) if public_url else None
    if account := config.twilio():
        twilio = Twilio(account)
        channels.append(SmsChannel(twilio, web, links))
        if public_url:
            channels.append(VoiceChannel(twilio, web, public_url, links))
    if sys.stdin.isatty():  # not when deployed
        channels.insert(0, CliChannel())
    gateway = Gateway(channels, store)
    orchestrator = Orchestrator(
        quick,
        store,
        tasks,
        gateway,
        researcher,
        connectors,
        devices,
        Whereabouts(store, web, links),
        threads,
        prompts.live("orchestrator"),
        twin,
        schedule,
        coder,
        tools,
    )
    twin.notify = orchestrator.handle
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(web, gateway, orchestrator, twin, threads, schedule, interrupted))
    print()


async def _run(
    web: Web,
    gateway: Gateway,
    orchestrator: Orchestrator,
    twin: Twin,
    threads: Threads,
    schedule: Schedule,
    interrupted: list[Task],
) -> None:
    async with asyncio.TaskGroup() as group:
        group.create_task(web.serve())
        group.create_task(gateway.run(orchestrator.handle))
        group.create_task(twin.watch())
        group.create_task(threads.watch())
        group.create_task(schedule.watch(lambda job: orchestrator.handle(Scheduled(job))))
        for task in interrupted:  # a deploy mid-task: whoever's waiting on it hears, and it may be retried
            group.create_task(orchestrator.handle(TaskUpdate(task)))


main()
