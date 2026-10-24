# Context twin

A second brain of the owner's personal context: who is in their life, what they want and can't stand, how they decide. It learns from everything they do with Archangel; every task consults it first. M12 in the roadmap. Agreed with the owner 2026-10-24.

## The graph

- **A node is a page:** markdown in the database, short ("Helen") or long ("Uber"). It holds facts, standing rules and, later, how-tos. A node with an empty page is a **router**: a name and links only, cheap to pass through (Travel, Work, People). Writing a page turns a router into a page node; nothing converts.
- **A link has a preview:** the target, a relation, and 5–10 words written from this side, saying why you'd follow it (`→ Uber "rides, receipts, business ↔ personal"`). An agent at a node reads its page and the previews, and opens only what it needs.
- **You** is the root: name, date of birth, home, allergies, the rules that must never be missed. Everything is about two links from it; depth comes from cross-links, not nesting.
- **Hubs, not duplicates.** Uber is one node, linked from Travel, Work and Personal. Names and aliases ("Mum" = Helen) are unique, enforced by the database, so the writer links to what exists.
- **The source, not the result.** A date of birth, not an age; "moved to London in March 2024", not "two years". Dates are absolute; a temporary fact carries its end date. Agents know today's date.
- **Placed as high as it holds.** "No morning flights" sits on Flights; once it holds for trains too, it moves up to Travel.

## Two agents

- **Reader** (read-only, fast model, no reasoning). Before any task it walks from You, or straight to a named node, and writes the task a briefing of a few lines. Mid-task, any agent can `ask_twin(question)` ("a sign-in page: what does the owner want?"). The orchestrator sees only the You page.
- **Writer** (main model, reasoning). Reads an episode (the conversation since its last read, once it's been quiet 10 minutes, which includes the owner's answers to tasks; or "remember…" / "forget…" at once) and edits the graph, or does nothing, which is the usual outcome. Keeps only what still holds next month and changes how a future task is done: "I hate morning flights" yes, "I'm in the mood for Indian" no. An answer that generalises ("always rate them 5 stars") is the richest source. Every edit quotes the owner's words. It can't finish with a stale preview. What it noted goes to the orchestrator, which tells the owner in a line (by text rather than a call).

Payments, sending and deleting always ask, whatever the twin holds.

## Code

`agent/twin/`:

| File | Role |
|---|---|
| `graph.py` | `Graph`: the only code on the graph's tables. `open(name)` is what a model sees at a node. Keeps the invariants: unique names, every edit recorded with its source, stale previews findable. |
| `reader.py` | `brief(goal)`, `ask(question)`. Tools: `open`, `find`. |
| `writer.py` | `notice(episode)`. Read tools plus edits. |
| `__init__.py` | `Twin`: when the writer reads (quiet conversations, `remember`), one episode at a time. |

Storage: four tables in `data/archangel.db` (`twin_nodes`, `twin_names`, `twin_links`, `twin_changes`; and `twin_read`, how far the writer has read), on the agent's Railway volume. No graph database: every query is "a node and its links", and the model chooses the path.

## Panel

A Context twin tab (`/twin`): the graph in React Flow, laid out by d3-force (hubs settle in the middle; what's placed stays put); routers dashed, page nodes solid. The owner shapes it by hand too: add nodes, drag one onto another to link them, edit a link's relation and preview, rename, aliases, edit or delete a page. Click a node: its page, links, and history with the owner's words. Later: nodes the reader opens light up live.

## Build order

1. `Graph`.
2. Writer: finished tasks, quiet chat, remember / forget, a "Noted: …" line to the owner.
3. The Twin tab.
4. Reader: briefings, `ask_twin`, You in the orchestrator's prompt.
5. Live, then: nightly tidying (merges, tendencies, fading), skills, search as an entry point.
