# Control panel

`apps/panel/`: React + Vite + plain CSS. Polls the agent's API every second (`channels/panel.py` serves it). Its buttons send owner messages, so approving from it costs one model call.

## Pages

Real paths (`route.tsx`); the agent serves the app at each one (`web.PAGES`), so a new page goes in both.

| Path | What |
|---|---|
| `/chat` | The conversation (below). |
| `/tasks`, `/tasks/{id}` | What's scheduled above the tasks, each with Cancel; a task's steps and screens. |
| `/threads`, `/threads/{id}` | Threads, open or closed, how full; one's summary, tasks and everything said ([threads.md](threads.md#panel)). |
| `/cockpit` | Live activity (below). |
| `/twin` | The context twin's graph, editable (below). |
| `/architecture` | How an event moves through Archangel (below). |
| `/evals` | The twin's eval runs. Local agent only, never public. |
| `/prompts` | Every prompt, editable; an edit wins from the agent's next run. The writing styles' texts below them (who each is for stays in its file). |
| `/connectors` | The connectors, and the quick tools beside them (on or off, their settings). The only page without the chat beside it. |

Web addresses in what's said, task reports, steps and thread summaries are links (`linked.tsx`).

## Look

Black, JetBrains Mono to read, Silkscreen (8/12/16px only) for labels, one acid accent, amber only when the owner is needed. A state changes colour, never size. The rules are at the top of `styles.css`.

Phones (≤760px, `useNarrow()`, the same breakpoint in `styles.css`): one column at a time, Chat its own tab and the default, Tasks as list then detail, the cockpit graph top to bottom above the timeline.

Hovering the name in the header shows the commit running and how long ago it was deployed (`/api/version`, from Railway's `RAILWAY_GIT_*`); lit for 10 minutes after a deploy.

## Chat

Enter sends, Shift+Enter is a new line. Pictures are attached with +, pasted, or dropped on the chat; each shows as a thumbnail (× takes it off) and goes with the text, or alone. Up to 4 a message, 5 MB each (the agent's cap): a bigger photo, or a type models don't read, is redrawn as a JPEG at most 2048px a side first. The dropdown chooses what it shows:

- **Main**: the whole conversation. Where it moves to another thread there's a rule with the thread's name, a link to it. The owner's newest line isn't filed yet when it arrives, so it's taken to be in its answer's thread.
- **An open thread**: only that thread's lines. What's said here is sent with the thread, so the router doesn't choose (it only judges a wrap-up), and a closed one reopens. Closed threads are on the Threads page.

## Cockpit

The live activity stream (`/api/activity`, `activity.py`) drawn with React Flow, laid out by dagre from who calls whom.

- Fitted to the layout's own bounds: React Flow's measurements lag new parts.
- Parts are kept in React Flow's state. Rebuilt each tick, they lost their measurements and the graph vanished under steady traffic.
- The map (parts, roles, who calls whom) is kept in the browser, so a refresh starts from all of it, not just the agent's last few notices. Forget starts it afresh.
- Click a notice to read it in full (the agent keeps 2000 characters of each).
- The context twin is a part of its own: who asks the reader or hands the writer a conversation (`transcript`), and each page they open or edit (`open Uber`, `link Uber → Work`), each a call to `twin`.

## Context twin

The twin's graph ([twin.md](twin.md)) with React Flow, laid out by d3-force.

- The layout runs to rest at once. Placed nodes stay put and only new ones move, then a collide-only pass makes room. Tidy lays it all out afresh.
- Nodes are kept in React Flow's state: a fresh node object hides until measured.
- Every node always has a target handle, or React Flow drops its edges.
- The Delete key is off; deleting is the page's button.
- Routers dashed, the open node's links lit, stale previews dashed. Nodes the reader or writer opens or edits light up live (the activity stream's calls to `twin`).

The owner edits it by hand: + Node, drag a node's grip onto another to link them, click a line to edit its relation and preview or unlink; on a node's page, rename it, add or remove aliases, edit or delete its page. Edits are recorded as from the panel.

## Architecture

A flowchart of how an event moves through Archangel, decisions as pointed tags. `blueprint.ts` is written by hand from the code: keep it true when the flow changes. Laid out once by dagre, wires along dagre's routes; loops back are left out of the layout and dashed. Click a part for what it does, its file and its wires. Trace plays scenarios through it a step at a time, lighting the path and following it.

## Evals

Local only (`evals_tab.py`). Lists the twin's eval runs (`evals/twin/results/`) and starts one (`evals.twin.run` in its own process, writing its file as runs finish). Shows runs with pass rates, a case-by-run grid with the change since the run before, and one run opened: what went in, every check (and the judge's reason), the reader's answer, the writer's edits as before/after, and the graph it ended with (added, changed and read nodes marked; click one for its page against how it started).

## Sounds

`sounds.ts`, files in `public/sounds/<cue>.wav`, made with earshot and chosen by the owner. Presses, tabs, the owner's own scrolling, switches, messages each way, approvals, a task needing them or done, and the cockpit's tx/rx. Web Audio, so a click lands with the press. "Sound on/off" in the header, kept per browser. `data-sound` on an element picks its cue, or `""` for none.
