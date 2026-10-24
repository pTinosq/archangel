"""The context twin in a terminal, on a real model and a graph of its own: `just twin [graph]`.

Kept in `data/twin-sandbox.db` between sessions, apart from Archangel's own. Start from a graph in `graphs/` (`load
alex`), talk to the writer and the reader, see what each did, and keep a graph you've grown as a new fixture
(`save name`).
"""

import asyncio
import readline  # noqa: F401  (line editing and history for input())
import sqlite3
import sys
import time
from pathlib import Path

from agent.twin.writer import Episode
from evals.twin import GRAPHS, draw, load, models, review, save, twin

DB = Path("data/twin-sandbox.db")

HELP = """\
  hear <what the owner said>   the reader, as before the orchestrator replies
  brief <task goal>            the reader, briefing a task
  ask <question>               the reader, as ask_twin
  say <what the owner said>    the writer: what it keeps, if anything
  show [node]                  the whole graph, or one node as models see it
  history [node]               edits, newest first
  review                       a model reviews the whole graph against the design
  load <graph>                 start again from graphs/<graph>.toml ({graphs})
  save <graph>                 keep this graph as graphs/<graph>.toml
  reset                        an empty graph
  quit"""


def fresh() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    return sqlite3.connect(DB)


async def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else None
    graph, reader, writer = twin(fresh() if start else sqlite3.connect(DB), GRAPHS / f"{start}.toml" if start else None)
    graphs = ", ".join(p.stem for p in sorted(GRAPHS.glob("*.toml")))
    print(f"The context twin, {len(graph.nodes())} nodes. help for commands.")
    while True:
        try:
            line = input("twin> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        command, _, rest = line.partition(" ")
        started = time.monotonic()
        try:
            if command in ("hear", "brief", "ask") and rest:
                graph.opened.clear()
                answer = await getattr(reader, command)(rest)
                opened = graph.opened[1:]  # the root comes with the question
                print(f"opened: {', '.join(opened) or 'nothing'}  ({time.monotonic() - started:.1f}s)")
                print(answer or "(nothing relevant)")
            elif command == "say" and rest:
                before = len(graph.history())
                note = await writer.notice(Episode(rest, "sandbox"))
                changes = graph.history()[: len(graph.history()) - before][::-1]
                print(f"({time.monotonic() - started:.1f}s) {note or 'Nothing kept.'}")
                for c in changes:
                    print(f"  {c.node}: {c.what}" + (f"\n    {c.after.strip()}" if c.after.strip() else ""))
            elif command == "show":
                print(graph.open(rest) if rest else draw(graph))
            elif command == "history":
                for c in graph.history(rest or None)[:30]:
                    print(f"  {c.node}: {c.what} ({c.origin})" + (f' "{c.said}"' if c.said else ""))
            elif command == "review":
                found = await review(models()[1], graph)
                print(f"{found.score}/5")
                print("\n".join([*(f"  + {s}" for s in found.strengths), *(f"  - {i}" for i in found.issues)]))
            elif command == "load" and rest:
                graph, reader, writer = twin(fresh(), GRAPHS / f"{rest}.toml")
                print(f"{len(graph.nodes())} nodes.")
            elif command == "save" and rest:
                save(graph, GRAPHS / f"{rest}.toml")
                print(f"Saved graphs/{rest}.toml")
            elif command == "reset":
                graph, reader, writer = twin(fresh(), None)
                print("An empty graph: just You.")
            elif command in ("quit", "exit"):
                return
            elif line:
                print(HELP.format(graphs=graphs))
        except Exception as error:  # a bad name, a model failing: say so, keep going
            print(f"{type(error).__name__}: {error}")


if __name__ == "__main__":
    asyncio.run(main())
