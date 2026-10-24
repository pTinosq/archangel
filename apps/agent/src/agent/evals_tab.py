"""The panel's Evals tab, on a local agent only: the context twin's eval results (apps/agent/evals/twin), and starting
a run. Runs are `evals.twin.run` in a process of their own, writing their file as they go; this only reads them.
Never deployed: evals are for working on the twin, and cost model calls.
"""

import asyncio
import json
import re
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

AGENT = Path(__file__).parents[2]  # apps/agent, where `evals` is importable from
EVALS = AGENT / "evals" / "twin"
RESULTS = EVALS / "results"


class Start(BaseModel):
    filter: str = ""
    runs: int = 3


def routes(app: FastAPI) -> None:
    running: set[asyncio.subprocess.Process] = set()

    @app.get("/api/evals")
    async def evals() -> dict:
        cases = tomllib.loads((EVALS / "cases.toml").read_text())["case"]
        return {"cases": [c["name"] for c in cases], "results": [_summary(p) for p in _files()]}

    @app.get("/api/evals/{id}")
    async def result(id: str) -> dict:
        path = RESULTS / f"{id}.json"
        if not re.fullmatch(r"[\w-]+", id) or not path.is_file():
            raise HTTPException(404, f"No eval run {id}.")
        return json.loads(path.read_text())

    @app.post("/api/evals")
    async def start(wanted: Start) -> dict:
        id = f"{datetime.now():%Y%m%d-%H%M%S}"
        command = [sys.executable, "-m", "evals.twin.run", wanted.filter, "--runs", str(wanted.runs), "--id", id]
        process = await asyncio.create_subprocess_exec(
            *command, cwd=AGENT, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        running.add(process)
        asyncio.create_task(process.wait()).add_done_callback(lambda _: running.discard(process))
        return {"id": id}


def _files() -> list[Path]:
    return sorted(RESULTS.glob("*.json"), reverse=True) if RESULTS.is_dir() else []


def _summary(path: Path) -> dict:
    try:
        report = json.loads(path.read_text())
        results = report["results"]
        return {
            "id": report["id"],
            "started": report["started"],
            "finished": report["finished"],
            "model": report["model"],
            "filter": report["filter"],
            "total": len(report["cases"]) * report["runs"],
            "done": len(results),
            "passed": sum(r["passed"] for r in results),
        }
    except (KeyError, ValueError, TypeError):  # from before the tab, or half-written
        return {"id": path.stem, "unreadable": True}
