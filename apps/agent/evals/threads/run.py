"""Runs the thread router's evals on a real model: `just threads-eval [name filter] [--runs N]`.

Each case starts from a fresh copy of the made-up threads, aged as `cases.toml` says, and runs several times (models
vary run to run, so a case is a pass rate). Runs go side by side. Every run is saved to `results/<id>.json`.
"""

import argparse
import asyncio
import json
import logging
import sqlite3
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pydantic_ai
from pydantic_ai import ModelRequest, ModelResponse, TextPart

from agent import config
from agent.models import openrouter
from agent.threads import RECENT
from agent.threads.router import Router
from agent.threads.store import ThreadStore

HERE = Path(__file__).parent
RESULTS = HERE / "results"
PARALLEL = 6


@dataclass
class Run:
    case: str
    run: int
    chose: str = ""
    """The key of the thread it chose, or "new" (and the new title)."""
    closes: bool = False
    passed: bool = False
    seconds: float = 0.0
    error: str = ""


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def build(threads: list[dict], case: dict) -> tuple[ThreadStore, dict[int, str]]:
    """The made-up threads, oldest first, each said in (and summarised) when its age says. Returns them by id → key."""
    clock, started = Clock(), time.time()
    store = ThreadStore(sqlite3.connect(":memory:"), clock)
    keys = {}
    for spec in sorted(threads, key=_age, reverse=True):
        clock.now = started - _age(spec)
        thread = store.new(spec["title"])
        keys[thread.id] = spec["key"]
        turns = [*spec.get("turns", []), *(case.get("add", []) if case.get("add_to") == spec["key"] else [])]
        store.save(thread.id, [message for said, answer in turns for message in _turn(said, answer)], size=500)
        if summary := spec.get("summary"):
            store.summarise(thread.id, spec["title"], summary, clock.now)
        if not spec.get("open"):
            store.close(thread.id)
    return store, keys


async def run(case: dict, number: int, threads: list[dict], router_model) -> Run:
    result = Run(case["name"], number)
    store, keys = build(threads, case)
    started = time.monotonic()
    try:
        choice = await Router(router_model, store).choose(f"(via sms) {case['says']}", store.recent(RECENT))
    except Exception as error:
        result.error = repr(error)
        return result
    result.seconds = round(time.monotonic() - started, 1)
    chose = keys[choice.thread] if choice.thread else "new"
    result.chose = chose if choice.thread else f"new: {choice.title}"
    result.closes = choice.closes
    wanted = [option.strip() for option in case["thread"].split("|")]
    closes = case.get("closes", False)
    result.passed = chose in wanted and (closes == "either" or choice.closes == closes)
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filter", nargs="?", default="", help="only cases whose name contains this")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--id", default=f"{datetime.now():%Y%m%d-%H%M%S}", help="the results file's name")
    args = parser.parse_args()
    logging.getLogger("agent").setLevel(logging.ERROR)
    pydantic_ai.BANNER_ENABLED = False

    spec = tomllib.loads((HERE / "cases.toml").read_text())
    cases = [c for c in spec["case"] if args.filter in c["name"]]
    router_model = openrouter(config.model(), think=False)  # as `__main__` makes it
    limit = asyncio.Semaphore(PARALLEL)

    async def one(case: dict, number: int) -> Run:
        async with limit:
            return await run(case, number, spec["thread"], router_model)

    print(f"{len(cases)} cases × {args.runs} runs on {config.model()}…", flush=True)
    runs = await asyncio.gather(*(one(c, n) for c in cases for n in range(args.runs)))

    for case in cases:
        mine = [r for r in runs if r.case == case["name"]]
        passed = sum(r.passed for r in mine)
        seconds = sorted(r.seconds for r in mine)[len(mine) // 2]
        print(f"{'✓' if passed == len(mine) else '✗'} {passed}/{len(mine)}  {seconds:>4}s  {case['name']}")
        for r in mine:
            if not r.passed:
                print(f"      {r.error or f'chose {r.chose}' + (', closes' if r.closes else '')}")
    print(f"\n{sum(r.passed for r in runs)}/{len(runs)} runs passed")

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{args.id}.json"
    out.write_text(json.dumps({"model": config.model(), "cases": cases, "runs": [asdict(r) for r in runs]}, indent=1))
    print(f"Every run: {out}")


def _turn(said: str, answer: str) -> list:
    return [ModelRequest.user_text_prompt(f"(via sms) {said}"), ModelResponse(parts=[TextPart(answer)])]


def _age(spec: dict) -> float:
    return 86400 * spec.get("days_ago", 0) + 3600 * spec.get("hours_ago", 0) + 60 * spec.get("minutes_ago", 0)


if __name__ == "__main__":
    asyncio.run(main())
