"""Runs the twin's evals on a real model: `just twin-eval [name filter] [--runs N]`, or from the panel's Evals tab.

Each case runs several times on a fresh copy of its graph (models vary run to run, so a case is a pass rate, not a
pass); runs go side by side. Plain checks are code; criteria a model must weigh go to a judge. Everything is saved
to `results/<id>.json` as each run finishes (the Evals tab shows it as it fills): the cases, the graphs they start
from, and per run its input, answer, the writer's edits, every check, and the graph it ended with.
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

from agent import config
from agent.twin.graph import ROOT, Graph
from agent.twin.writer import Episode
from evals.twin import GRAPHS, HERE, Watched, draw, judge, load, models, twin

PARALLEL = 6
RESULTS = HERE / "results"
READS = ("hear", "brief", "ask")


@dataclass
class Check:
    name: str
    passed: bool
    why: str = ""
    """The judge's reason, or what was found."""


@dataclass
class Run:
    case: str
    run: int
    kind: str = ""
    """hear, brief or ask: how the reader was asked; "" if only the writer ran."""
    input: str = ""
    answer: str = ""
    opened: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    graph: dict = field(default_factory=dict)
    """The graph afterwards."""
    seconds: float = 0.0
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(c.passed for c in self.checks)


async def run(case: dict, number: int, judge_model) -> Run:
    result, started = Run(case["name"], number), time.monotonic()
    try:
        graph, reader, writer = twin(sqlite3.connect(":memory:"), GRAPHS / f"{case.get('graph', 'alex')}.toml")
        before, nodes_before = len(graph.history()), len(graph.nodes())
        for said in case.get("say", []):
            result.notes.append(await writer.notice(Episode(said, "chat")))
        changes = graph.history()[: len(graph.history()) - before][::-1]
        result.changes = [
            {"node": c.node, "what": c.what, "before": c.before, "after": c.after, "said": c.said} for c in changes
        ]

        if kind := next((k for k in READS if k in case), ""):
            graph.opened.clear()
            result.kind, result.input = kind, case[kind]
            result.answer = await getattr(reader, kind)(case[kind])
            result.opened = graph.opened[1:] if graph.opened[:1] == [ROOT] else graph.opened  # the root comes free
        result.seconds = round(time.monotonic() - started, 1)
        result.graph = shape(graph)
        check(case, result, len(graph.nodes()) - nodes_before, graph)

        if criteria := case.get("judge"):
            evidence = [
                f"The owner said (to the writer, in order): {case['say']}" if case.get("say") else "",
                f"The writer's changes: {_changes(result) or 'none'}" if case.get("say") else "",
                f"The reader was given ({kind}): {result.input}\nIts answer: {result.answer or '(nothing relevant)'}"
                if kind
                else "",
                f"The graph afterwards:\n{draw(graph)}",
            ]
            for verdict in await judge(judge_model, criteria, "\n\n".join(part for part in evidence if part)):
                result.checks.append(Check(f"judge: {verdict.criterion}", verdict.passed, verdict.why))
    except Exception as error:
        result.error = f"{type(error).__name__}: {error}"
    return result


def check(case: dict, result: Run, new_nodes: int, graph: Graph) -> None:
    answer, add = result.answer.casefold(), result.checks.append
    for wanted in case.get("include", []):
        add(Check(f"includes {wanted}", any(w.strip().casefold() in answer for w in wanted.split("|"))))
    for unwanted in case.get("exclude", []):
        add(Check(f"excludes {unwanted}", unwanted.casefold() not in answer))
    if case.get("nothing"):
        add(Check("says nothing", result.answer == ""))
    if (most := case.get("opens_at_most")) is not None:
        add(Check(f"opens ≤ {most}", len(result.opened) <= most, f"opened {len(result.opened)}"))
    if case.get("unchanged"):
        add(Check("unchanged", not result.changes, f"{len(result.changes)} edits"))
    if (most := case.get("new_nodes_at_most")) is not None:
        add(Check(f"new nodes ≤ {most}", new_nodes <= most, f"added {new_nodes}"))
    if case.get("say"):  # open() gives each page its heading; one written into the page doubles it
        headed = [n.name for n in graph.nodes() if n.page.lstrip().startswith("#")]
        add(Check("pages without headings", not headed, ", ".join(headed)))


def shape(graph: Graph) -> dict:
    """The graph as the panel draws it."""
    return {
        "nodes": [{"name": n.name, "page": n.page, "aliases": list(n.aliases)} for n in graph.nodes()],
        "links": [asdict(link) for link in graph.links()],
    }


def _changes(result: Run) -> list[str]:
    return [f"{c['node']}: {c['what']}: {c['before']!r} → {c['after']!r}" for c in result.changes]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filter", nargs="?", default="", help="only cases whose name contains this")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--id", default=f"{datetime.now():%Y%m%d-%H%M%S}", help="the results file's name")
    args = parser.parse_args()
    logging.getLogger("agent").setLevel(logging.ERROR)  # a model's invalid call it then fixes is part of the run

    cases = [c for c in tomllib.loads((HERE / "cases.toml").read_text())["case"] if args.filter in c["name"]]
    graphs = {}
    for name in {c.get("graph", "alex") for c in cases}:
        start = Watched(sqlite3.connect(":memory:"))
        load(start, GRAPHS / f"{name}.toml")
        graphs[name] = shape(start)
    report = {
        "id": args.id,
        "started": time.time(),
        "finished": None,
        "model": config.model(),
        "filter": args.filter,
        "runs": args.runs,
        "cases": cases,
        "graphs": graphs,
        "results": [],
    }
    out = RESULTS / f"{args.id}.json"
    RESULTS.mkdir(exist_ok=True)

    def save() -> None:
        partial = out.with_suffix(".tmp")  # whole or not at all: the panel may be reading it
        partial.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=_plain))
        partial.replace(out)

    save()
    judge_model = models()[1]
    limit = asyncio.Semaphore(PARALLEL)

    async def one(case: dict, number: int) -> Run:
        async with limit:
            result = await run(case, number, judge_model)
        report["results"].append(result)
        save()
        return result

    print(f"{len(cases)} cases × {args.runs} runs…", flush=True)
    runs = await asyncio.gather(*(one(c, n) for c in cases for n in range(args.runs)))
    report["finished"] = time.time()
    save()

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
            for c in r.checks:
                if not c.passed:
                    print(f"      ✗ {c.name}" + (f": {c.why}" if c.why else ""))
            if not r.passed and r.answer:
                print(f"        answer: {r.answer[:300]!r}")
    print(f"\n{total}/{len(runs)} runs passed")
    print(f"Every run: {out} (and the panel's Evals tab, locally)")


def _plain(value: object) -> dict:
    """A run as JSON, with whether it passed."""
    return {**asdict(value), "passed": value.passed} if isinstance(value, Run) else asdict(value)


if __name__ == "__main__":
    asyncio.run(main())
