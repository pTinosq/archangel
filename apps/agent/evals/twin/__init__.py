"""The context twin on a real model, off to the side: a sandbox to try it by hand (`just twin`) and evals to measure
it (`just twin-eval`). Graphs are TOML files in `graphs/` (a made-up owner: nothing real is committed); cases are in
`cases.toml`. Costs real model calls, so it isn't part of `just test`.
"""

import sqlite3
import tomllib
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import config
from agent.models import openrouter
from agent.twin.graph import ROOT, Graph
from agent.twin.reader import Reader
from agent.twin.writer import Writer

HERE = Path(__file__).parent
GRAPHS = HERE / "graphs"


class Watched(Graph):
    """A graph that remembers which pages were opened, as the reader walks it."""

    def __init__(self, db: sqlite3.Connection):
        super().__init__(db)
        self.opened: list[str] = []

    def open(self, name: str) -> str:
        self.opened.append(name)
        return super().open(name)


def models() -> tuple[Model, Model]:
    """The reader's and writer's models, as `__main__` makes them: the same model, the reader without reasoning."""
    return openrouter(config.model(), think=False), openrouter(config.model())


def twin(db: sqlite3.Connection, graph_file: Path | None) -> tuple[Watched, Reader, Writer]:
    graph = Watched(db)
    if graph_file:
        load(graph, graph_file)
    quick, model = models()
    return graph, Reader(quick, graph), Writer(model, graph)


def load(graph: Graph, path: Path) -> None:
    data = tomllib.loads(path.read_text())
    for node in data.get("node", []):
        page = node.get("page", "").strip()
        if graph.find(node["name"]):
            graph.write(node["name"], page, origin="fixture")
        else:
            graph.add(node["name"], page, origin="fixture")
        for alias in node.get("aliases", []):
            graph.alias(node["name"], alias, origin="fixture")
    for link in data.get("link", []):
        graph.link(link["from"], link["to"], link["relation"], link["preview"], origin="fixture")


def save(graph: Graph, path: Path) -> None:
    """The graph as a TOML file `load` reads back: build one in the sandbox, keep it as a fixture."""
    out = []
    for node in graph.nodes():
        out += ["[[node]]", f"name = {_quote(node.name)}"]
        if node.aliases:
            out.append(f"aliases = [{', '.join(_quote(a) for a in node.aliases)}]")
        out += [f"page = '''\n{node.page.strip()}\n'''" if node.page.strip() else 'page = ""', ""]
    for link in graph.links():
        out += ["[[link]]", f"from = {_quote(link.source)}", f"to = {_quote(link.target)}"]
        out += [f"relation = {_quote(link.relation)}", f"preview = {_quote(link.preview)}", ""]
    path.write_text("\n".join(out))


def draw(graph: Graph) -> str:
    """The whole graph as models read it, node by node, the root first."""
    names = [ROOT] + [n.name for n in graph.nodes() if n.name != ROOT]
    return "\n\n".join(Graph.open(graph, name) for name in names)  # not Watched.open: drawing isn't reading


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Verdict(BaseModel):
    criterion: str
    passed: bool
    why: str
    """One sentence."""


JUDGE = """You grade one test of an AI assistant's "context twin": a graph of markdown pages about its owner, read by \
a reader agent (which briefs other agents in a few lines, or says nothing) and grown by a writer agent (which keeps \
only what's lasting about the owner and usually changes nothing). For each criterion, decide whether it holds, from \
the evidence given. Be strict about the criterion, and only the criterion: style you'd change isn't a failure."""


async def judge(model: Model, criteria: list[str], evidence: str) -> list[Verdict]:
    agent = Agent(model, output_type=list[Verdict], instructions=JUDGE)
    prompt = f"Today is {date.today():%A %d %B %Y}.\n\n{evidence}\n\nCriteria, one verdict each, in order:\n"
    result = await agent.run(prompt + "\n".join(f"- {c}" for c in criteria))
    return result.output


class Review(BaseModel):
    score: int = Field(ge=1, le=5)
    """1 (a mess) to 5 (exactly as designed)."""
    strengths: list[str]
    issues: list[str]
    """Each names the nodes concerned and what to change."""


REVIEW = """You review an AI assistant's "context twin": a graph of markdown pages about its owner, which agents \
walk from the root, choosing links by their previews, before acting for the owner. Judge it against its design:
- One node per thing (no duplicates under other names; aliases instead), linked from every area it touches (hubs).
- Shallow: everything about two links from the root, through routers (empty pages); cross-links, not deep nesting.
- Each fact as high as it holds, and in one place only.
- The source, not the result: dates of birth, not ages; absolute dates; temporary facts carry their end date.
- Lasting things only: no moods or one-offs.
- Previews say in a few words what's at the other end that matters from here, so a reader can choose without opening.
- Pages are short markdown bullets; nothing contradicts anything else.
Score it, then list strengths and concrete issues."""


async def review(model: Model, graph: Graph) -> Review:
    agent = Agent(model, output_type=Review, instructions=REVIEW)
    return (await agent.run(f"Today is {date.today():%A %d %B %Y}.\n\n{draw(graph)}")).output
