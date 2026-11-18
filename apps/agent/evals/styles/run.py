"""Runs the writing styles' evals on a real model: `just styles-eval [name filter] [--runs N]`.

Each case is a task's goal that writes to someone, given to a writer with `writing_style` and the pilots' line about
it, on the built-in styles. The draft is checked in code for what every style forbids (stock openers and closers, AI
words, emoji, length, a greeting where none belongs) and for the facts kept exact, and by a judge for the rest. Models
vary run to run, so a case is a pass rate. Every run is saved to `results/<id>.json`.
"""

import argparse
import asyncio
import json
import logging
import re
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pydantic_ai
from pydantic import BaseModel
from pydantic_ai import Agent, ToolCallPart

from agent import config
from agent.models import openrouter
from agent.styles import Audience, Styles

HERE = Path(__file__).parent
RESULTS = HERE / "results"
PARALLEL = 6

WRITER = """You do one of the owner's tasks: write the message it asks for. Before you write it, call writing_style \
(to: their name, or "owner"; via: the app; asked: how the task says it should sound, if it does) and write as it \
says. Words the task gives in quotes are sent as given. Reply with the message alone, exactly as it would be sent."""

JUDGE = """You grade a message an AI assistant wrote for its owner, to send to someone. For each criterion, decide \
whether it holds, from the task and the message. Be strict about the criterion, and only the criterion: wording \
you'd change isn't a failure."""

# What no style allows, said anywhere (base.md). Lower case, straight apostrophes.
STOCK = [
    "great question", "certainly!", "of course!", "i hope you're well", "i hope you are well", "hope this finds",
    "hope you're doing well", "let me know if you need", "happy to help", "feel free to", "don't hesitate",
    "hope this helps", "delve", "tapestry", "leverage", "seamless", "robust", "it's important to note",
    "in conclusion", "i understand",
]  # fmt: skip
GREETING = re.compile(r"^(hi|hey|hello|dear|good (morning|afternoon|evening))\b", re.IGNORECASE)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


@dataclass
class Check:
    name: str
    passed: bool
    why: str = ""


@dataclass
class Run:
    case: str
    run: int
    draft: str = ""
    asked_for: list[dict] = field(default_factory=list)
    """What it passed `writing_style`, each call."""
    checks: list[Check] = field(default_factory=list)
    seconds: float = 0.0
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(c.passed for c in self.checks)


class Verdict(BaseModel):
    criterion: str
    passed: bool
    why: str
    """One sentence."""


async def run(case: dict, number: int, styles: Styles, model) -> Run:
    result, started = Run(case["name"], number), time.monotonic()
    try:
        writer = Agent(model, instructions=WRITER, toolsets=[styles.tools])
        done = await writer.run(case["goal"])
        result.seconds = round(time.monotonic() - started, 1)
        result.draft = done.output.strip()
        result.asked_for = [
            part.args_as_dict()
            for message in done.all_messages()
            for part in message.parts
            if isinstance(part, ToolCallPart) and part.tool_name == "writing_style"
        ]
        check(case, result, styles)
        if criteria := case.get("judge"):
            judge = Agent(model, output_type=list[Verdict], instructions=JUDGE)
            prompt = f"The task: {case['goal']}\n\nThe message:\n{result.draft}\n\nCriteria, one verdict each:\n"
            verdicts = (await judge.run(prompt + "\n".join(f"- {c}" for c in criteria))).output
            result.checks += [Check(f"judge: {v.criterion}", v.passed, v.why) for v in verdicts]
    except Exception as error:
        result.error = f"{type(error).__name__}: {error}"
    return result


def check(case: dict, result: Run, styles: Styles) -> None:
    draft, add = result.draft, result.checks.append
    plain = draft.casefold().replace("’", "'")
    got = [styles.choose(Audience.of(call.get("to", ""), call.get("via", ""))).name for call in result.asked_for]
    add(Check(f"style {case['style']}", case["style"] in got, ", ".join(got) or "never asked"))
    if case.get("asked"):
        add(Check("passes on how it should sound", any(c.get("asked", "").strip() for c in result.asked_for)))
    found = [phrase for phrase in STOCK if phrase in plain]
    add(Check("no stock phrases", not found, ", ".join(found)))
    if not case.get("emoji"):
        add(Check("no emoji", not EMOJI.search(draft), "".join(EMOJI.findall(draft))))
    if (most := case.get("max_words")) is not None:
        words = len(draft.split())
        add(Check(f"≤ {most} words", words <= most, f"{words} words"))
    if (greets := case.get("greets")) is not None:
        body = re.sub(r"^subject:.*\n+", "", draft, flags=re.IGNORECASE)  # an email may start with its subject
        add(Check("greets" if greets else "no greeting", bool(GREETING.match(body)) == greets))
    for wanted in case.get("contains", []):
        add(Check(f"contains {wanted}", any(w.strip().casefold() in plain for w in wanted.split("|"))))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filter", nargs="?", default="", help="only cases whose name contains this")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--id", default=f"{datetime.now():%Y%m%d-%H%M%S}", help="the results file's name")
    args = parser.parse_args()
    logging.getLogger("agent").setLevel(logging.ERROR)
    pydantic_ai.BANNER_ENABLED = False

    cases = [c for c in tomllib.loads((HERE / "cases.toml").read_text())["case"] if args.filter in c["name"]]
    styles, model = Styles(), openrouter(config.model())  # the built-in styles; the pilots' model, reasoning
    limit = asyncio.Semaphore(PARALLEL)

    async def one(case: dict, number: int) -> Run:
        async with limit:
            return await run(case, number, styles, model)

    print(f"{len(cases)} cases × {args.runs} runs on {config.model()}…", flush=True)
    runs = await asyncio.gather(*(one(c, n) for c in cases for n in range(args.runs)))

    for case in cases:
        mine = [r for r in runs if r.case == case["name"]]
        passed = sum(r.passed for r in mine)
        seconds = sorted(r.seconds for r in mine)[len(mine) // 2]
        print(f"{'✓' if passed == len(mine) else '✗'} {passed}/{len(mine)}  {seconds:>5}s  {case['name']}")
        for r in mine:
            if r.error:
                print(f"      error: {r.error}")
            for c in r.checks:
                if not c.passed:
                    print(f"      ✗ {c.name}" + (f": {c.why}" if c.why else ""))
            if not r.passed and r.draft:
                print(f"        draft: {r.draft[:300]!r}")
    print(f"\n{sum(r.passed for r in runs)}/{len(runs)} runs passed")

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{args.id}.json"
    report = {"model": config.model(), "cases": cases, "runs": [{**asdict(r), "passed": r.passed} for r in runs]}
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"Every run: {out}")


if __name__ == "__main__":
    asyncio.run(main())
