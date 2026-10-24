"""The twin's writer: reads what happened (an episode) and grows the graph, or, usually, leaves it alone."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from agent import logs
from agent.models import Instructions
from agent.twin.graph import ROOT, Graph

log = logging.getLogger(__name__)

INSTRUCTIONS = f"""You keep the owner's context twin: a graph of what's lasting about them: who's in their life, \
what they want and can't stand, how they decide. Each node is a markdown page (empty: a router, only links); each \
link has a relation and a few-word preview of what's there, from this side. {ROOT} is the root.

Change it only for what will still hold next month and would change how a future task is done: "I hate morning \
flights", "Mum is Helen", "always rate drivers 5 stars". Not moods or one-offs: "I fancy Indian tonight", "the \
cheaper one this time". Only what the owner said or plainly agreed to, never what you infer from what Archangel did. \
Usually there's nothing: then change nothing.

One node per thing, linked from every area it touches (Uber from Travel and Work): look before adding. Shallow: \
everything about two links from {ROOT}, through routers like People, Travel, Work; link sideways rather than nest. \
Put a fact as high as it holds (morning flights on Flights; on Travel once it holds for trains too). Write what \
won't go stale: a date of birth, not an age; absolute dates; an end date on anything temporary. Quote the owner in \
`said`. Pages are markdown: short bullets, a **label:** where it helps. After changing a page, rewrite the \
previews of links to it.

End with one short line for the owner on what you noted ("Noted: no morning flights."), or "Nothing." if you changed \
nothing."""


@dataclass(frozen=True)
class Episode:
    """Something that happened, as the writer reads it, and where it happened (for the graph's history)."""

    text: str
    origin: str


class Writer:
    def __init__(self, model: Model, graph: Graph, instructions: Instructions = INSTRUCTIONS):
        self._graph = graph
        self._agent = Agent(
            model,
            name="twin writer",
            deps_type=Episode,
            instructions=[instructions, lambda: f"Today is {date.today():%A %d %B %Y}."],
            tools=_tools(graph),
            retries=3,  # a refused edit or a stale preview is the model's to fix
        )

        @self._agent.output_validator
        async def no_stale_previews(output: str) -> str:
            if stale := graph.stale():
                links = "; ".join(f"{link.source} → {link.target}" for link in stale)
                raise ModelRetry(f"These previews no longer match their pages; rewrite them with link: {links}")
            return output

    async def notice(self, episode: Episode) -> str:
        """Learns what's worth keeping from `episode`. Returns a line for the owner if the graph changed, else ""."""
        before = len(self._graph.history())
        result = await logs.run(self._agent, episode.text, deps=episode)
        changed = len(self._graph.history()) > before
        log.info("Twin %s from %s: %s", "changed" if changed else "unchanged", episode.origin, result.output)
        return result.output.strip() if changed else ""


def _tools(graph: Graph) -> list:
    # Async, all of them: sync tools run on a thread, and SQLite belongs to the one that opened it.
    # A refused edit (a taken name, a missing node) goes back to the model to fix, not up to the caller.

    async def nodes() -> str:
        """Every node's name, and its aliases."""
        return "; ".join(n.name + (f" (also {', '.join(n.aliases)})" if n.aliases else "") for n in graph.nodes())

    async def open(name: str) -> str:
        """A node's page and its links, by name or alias."""
        try:
            return graph.open(name)
        except ValueError as error:
            return str(error)

    async def add(ctx: RunContext[Episode], name: str, page: str, said: str) -> str:
        """A new node. An empty page makes a router."""
        return attempt(lambda: graph.add(name, page, said=said, origin=ctx.deps.origin), f"Added {name}.")

    async def write(ctx: RunContext[Episode], name: str, page: str, said: str) -> str:
        """Replace a node's whole page (an empty one makes it a router)."""
        return attempt(lambda: graph.write(name, page, said=said, origin=ctx.deps.origin), f"Wrote {name}.")

    async def alias(ctx: RunContext[Episode], name: str, alias: str, said: str) -> str:
        """Another name for a node, e.g. Mum for Helen."""
        return attempt(lambda: graph.alias(name, alias, said=said, origin=ctx.deps.origin), "Done.")

    async def link(ctx: RunContext[Episode], source: str, target: str, relation: str, preview: str, said: str) -> str:
        """Link two nodes, or rewrite a link. `preview`: 5-10 words on what's at the target that matters from here."""
        return attempt(
            lambda: graph.link(source, target, relation, preview, said=said, origin=ctx.deps.origin), "Linked."
        )

    async def unlink(ctx: RunContext[Episode], source: str, target: str, said: str) -> str:
        return attempt(lambda: graph.unlink(source, target, said=said, origin=ctx.deps.origin), "Unlinked.")

    async def rename(ctx: RunContext[Episode], name: str, new: str, said: str) -> str:
        return attempt(lambda: graph.rename(name, new, said=said, origin=ctx.deps.origin), "Renamed.")

    async def merge(ctx: RunContext[Episode], name: str, into: str, said: str) -> str:
        """Two nodes that are one thing: write the combined page to `into` first; `name` becomes its alias."""
        return attempt(lambda: graph.merge(name, into, said=said, origin=ctx.deps.origin), "Merged.")

    async def delete(ctx: RunContext[Episode], name: str, said: str) -> str:
        """Remove a node that no longer holds, with its links."""
        return attempt(lambda: graph.delete(name, said=said, origin=ctx.deps.origin), "Deleted.")

    return [nodes, open, add, write, alias, link, unlink, rename, merge, delete]


def attempt(edit: Callable[[], object], done: str) -> str:
    try:
        edit()
    except ValueError as error:
        return f"Refused: {error}"
    return done
