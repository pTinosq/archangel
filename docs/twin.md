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

- **Reader** (read-only, fast model, no reasoning). Before any task it walks from You, or straight to a named node, and writes the task a briefing of a few lines. Mid-task, any agent can `ask_twin(question)` ("a sign-in page: what does the owner want?"). The orchestrator always has the You page, and before it answers anything the owner says, the reader walks the graph for what bears on it ("who is my mum?" opens People, then Mum), so it answers knowing them.
- **Writer** (main model, reasoning). Reads an episode (the conversation since its last read, once it's been quiet 10 minutes, which includes the owner's answers to tasks; or "remember…" / "forget…" at once) and edits the graph, or does nothing, which is the usual outcome. Keeps only what still holds next month and changes how a future task is done: "I hate morning flights" yes, "I'm in the mood for Indian" no. An answer that generalises ("always rate them 5 stars") is the richest source. Every edit quotes the owner's words. It can't finish with a stale preview. What it noted goes to the orchestrator, which tells the owner in a line (by text rather than a call).

Payments, sending and deleting always ask, whatever the twin holds.

## Code

`agent/twin/`:

| File | Role |
|---|---|
| `graph.py` | `Graph`: the only code on the graph's tables. `open(name)` is what a model sees at a node. Keeps the invariants: unique names, every edit recorded with its source, stale previews findable. |
| `reader.py` | `brief(goal)`, `ask(question)`. One tool, `open` (by name or alias); the root comes with the question. |
| `writer.py` | `notice(episode)`. Read tools plus edits. |
| `__init__.py` | `Twin`: when the writer reads (quiet conversations, `remember`), one episode at a time. |

Storage: four tables in `data/archangel.db` (`twin_nodes`, `twin_names`, `twin_links`, `twin_changes`; and `twin_read`, how far the writer has read), on the agent's Railway volume. No graph database: every query is "a node and its links", and the model chooses the path.

## Panel

A Context twin tab (`/twin`): the graph in React Flow, laid out by d3-force (hubs settle in the middle; what's placed stays put); routers dashed, page nodes solid. The owner shapes it by hand too: add nodes, drag one onto another to link them, edit a link's relation and preview, rename, aliases, edit or delete a page. Click a node: its page, links, and history with the owner's words. Nodes the reader or writer opens or edits light up live. The cockpit shows the twin as a part too: who asked the reader, what the writer was handed, and each page opened or edited.

## Evals

`just twin-eval` runs the reader and writer on the real model against a made-up owner (`apps/agent/evals/twin/`), each case 3 times; `just twin` is the same by hand. Checks are code where they can be (must say, mustn't say, pages opened, nothing changed, no new nodes), a model judge where they can't ("Rosa has her own node, linked from People"). First run, 2026-10-24: 36/57. Fixed from what it found: an explained "Nothing relevant: …" was passed on as a briefing; pages got a doubled `# Name` heading; derived facts were stored ("turns 30 on…"); a changed fact kept the old beside the new. Then 54/57, the rest the reader glancing at one page before saying nothing.

## Build order

1. ✅ `Graph`.
2. ✅ Writer: quiet conversations, remember / forget, a "Noted: …" line to the owner.
3. ✅ The Context twin tab, editable by hand.
4. ✅ Reader: briefings before phone and connectors tasks, `ask_twin` for the orchestrator, pilot and helper, the root in the orchestrator's prompt, nodes lit as they're read.
5. Next: try it live; nightly tidying (merges, tendencies, fading), skills, search as an entry point.
