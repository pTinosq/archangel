"""Runs the twin's evals on a real model: `just twin-eval [name filter] [--runs N]`.

Each case runs several times on a fresh copy of its graph (models vary run to run, so a case is a pass rate, not a
pass); runs go side by side. Plain checks are code; criteria a model must weigh go to a judge. Every run's input,
answer, the writer's changes and the verdicts are saved to `results/<time>.json`, to read and to compare.
"""

import argparse
import asyncio
import json
import logging
import sqlite3
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime

from agent.twin.graph import ROOT
from agent.twin.writer import Episode
from evals.twin import GRAPHS, HERE, draw, judge, models, twin

PARALLEL = 6


@dataclass
class Run:
    case: str
    answer: str = ""
    opened: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    why: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(self.checks.values())


async def run(case: dict, judge_model) -> Run:
    result, started = Run(case["name"]), time.monotonic()
    try:
        graph, reader, writer = twin(sqlite3.connect(":memory:"), GRAPHS / f"{case.get('graph', 'alex')}.toml")
        before, nodes_before = len(graph.history()), len(graph.nodes())
        for said in case.get("say", []):
            result.notes.append(await writer.notice(Episode(said, "chat")))
        changes = graph.history()[: len(graph.history()) - before][::-1]
        result.changes = [f"{c.node}: {c.what}: {c.before!r} → {c.after!r}" for c in changes]

        if kind := next((k for k in ("hear", "brief", "ask") if k in case), None):
            graph.opened.clear()
            result.answer = await getattr(reader, kind)(case[kind])
            result.opened = graph.opened[1:] if graph.opened[:1] == [ROOT] else graph.opened  # the root comes free
        result.seconds = round(time.monotonic() - started, 1)
        check(case, result, len(graph.nodes()) - nodes_before)
        if case.get("say"):  # open() gives each page its heading; one written into the page doubles it
            result.checks["pages without headings"] = not any(n.page.lstrip().startswith("#") for n in graph.nodes())

        if criteria := case.get("judge"):
            evidence = "\n\n".join(
                [
                    f"The owner said (to the writer, in order): {case['say']}" if case.get("say") else "",
                    f"The writer's changes: {result.changes or 'none'}" if case.get("say") else "",
                    f"The reader was given ({kind}): {case[kind]}\nIts answer: {result.answer or '(nothing relevant)'}"
                    if kind
                    else "",
                    f"The graph afterwards:\n{draw(graph)}",
                ]
            )
            for verdict in await judge(judge_model, criteria, evidence):
                result.checks[f"judge: {verdict.criterion}"] = verdict.passed
                result.why[f"judge: {verdict.criterion}"] = verdict.why
    except Exception as error:
        result.error = f"{type(error).__name__}: {error}"
    return result


def check(case: dict, result: Run, new_nodes: int) -> None:
    answer = result.answer.casefold()
    for wanted in case.get("include", []):
        result.checks[f"includes {wanted}"] = any(w.strip().casefold() in answer for w in wanted.split("|"))
    for unwanted in case.get("exclude", []):
        result.checks[f"excludes {unwanted}"] = unwanted.casefold() not in answer
    if case.get("nothing"):
        result.checks["says nothing"] = result.answer == ""
    if (most := case.get("opens_at_most")) is not None:
        result.checks[f"opens ≤ {most}"] = len(result.opened) <= most
    if case.get("unchanged"):
        result.checks["unchanged"] = not result.changes
    if (most := case.get("new_nodes_at_most")) is not None:
        result.checks[f"new nodes ≤ {most}"] = new_nodes <= most


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filter", nargs="?", default="", help="only cases whose name contains this")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    logging.getLogger("agent").setLevel(logging.ERROR)  # a model's invalid call it then fixes is part of the run
    cases = [c for c in tomllib.loads((HERE / "cases.toml").read_text())["case"] if args.filter in c["name"]]
    judge_model = models()[1]
    limit = asyncio.Semaphore(PARALLEL)

    async def limited(case):
        async with limit:
            return await run(case, judge_model)

    print(f"{len(cases)} cases × {args.runs} runs…", flush=True)
    runs = await asyncio.gather(*(limited(c) for c in cases for _ in range(args.runs)))

    total = 0
    for case in cases:
        mine = [r for r in runs if r.case == case["name"]]
        passed = sum(r.passed for r in mine)
        total += passed
        seconds = sorted(r.seconds for r in mine)[len(mine) // 2]
        print(f"{'✓' if passed == len(mine) else '✗'} {passed}/{len(mine)}  {seconds:>5}s  {case['name']}")
        for r in mine:
            if r.error:
                print(f"      error: {r.error}")
            for name, ok in r.checks.items():
                if not ok:
                    print(f"      ✗ {name}" + (f": {r.why[name]}" if name in r.why else ""))
            if not r.passed and r.answer:
                print(f"        answer: {r.answer[:300]!r}")
    print(f"\n{total}/{len(runs)} runs passed")

    out = HERE / "results" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps([asdict(r) for r in runs], indent=2, ensure_ascii=False))
    print(f"Every run: {out}")


if __name__ == "__main__":
    asyncio.run(main())
