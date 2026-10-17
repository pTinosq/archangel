# Roadmap

Each milestone ships something usable and proves one idea.

| # | Milestone | You can… | Proves |
|---|---|---|---|
| M0 ✅ | Bridge + relay | drive the phone over HTTP | cable-free phone control |
| M1 ✅ | Comms layer + CLI + brain | chat in the terminal, send images; idle conversations expire | channel abstraction, model-agnostic brain |
| M2 ✅ | Phone hands | "open WhatsApp and read my last message" | phone sub-agent: screenshot → act → verify, retries, step budget |
| M3 ✅ | Orchestrator + human-in-the-loop + escape hatches + durable state | talk while tasks run in the background; "Post this? [screenshot]" → "yes"; tasks stop when stuck; answer an approval after a restart | orchestrator/sub-agent split; approvals enforced in code; deferred, resumable runs |
| M4 ✅ | Fallback intelligence | a frozen or looping task gets diagnosed and unstuck without you | a second vision model only when the first misses or the pilot goes in circles |
| M5 ✅ | Control panel | watch tasks, runs and screenshots live in a local web app; chat from it; edit prompts | panel as a channel and a window onto the store |
| M6 ✅ | Cockpit | watch a request travel live: channel → orchestrator → task → pilot → Jev / guard / Luna → phone, and back | a lossy live activity feed that observes the system without slowing or changing it |
| M7 | Deploy | use Archangel from anywhere with the laptop closed: the agent and relay on Railway, the phone on the desk, the panel behind "Continue with GitHub" | runs detached from localhost; one owner, no auth platform |
| M8 | Telegram | message Archangel from Telegram | new channel, no orchestrator change |
| M9 | MCP tools | "summarise today's emails" without the phone | MCP toolsets beside phone tools, set up from the panel |
| M10 | Context twin | say "never show me sponsored restaurants" once; every later task obeys it | self-learning memory: rules captured from chat, retrieved before each task |
| M11 | Voice | call your Twilio number | LiveKit SIP as a channel |
| M12 | Autonomy | scheduled and event-triggered tasks | triggers as channels; Temporal if needed |

## M6 (done)

- **Announcing:** `agent/activity.py`, used like logging. `hop` for one-way messages, `call` for a request and its reply. No handles are passed and no signatures changed; deleting the calls removes the cockpit.
- **Ids in context:** context variables carry the request (set by the gateway per owner message), the task (set by `Tasks`) and the actor (set by each agent run). Everything a request leads to, including its tasks and their updates, carries its id.
- **Seams:** gateway, `logs.run` (each model call and tool call of every agent; guard, investigator and vision now run through it too), tasks, the pilot's calls to guard, vision and investigator, Jev, and `Phone._command`.
- **Lossy:** announcing never waits or raises. Each listener has a bounded queue and misses notices when it's full. The last 200 are kept for new listeners. Nothing is stored.
- **Stream:** `GET /api/activity` uses server-sent events, not a WebSocket. It's one-way by construction, needs no new dependency, and the browser reconnects it. A heartbeat lets open streams end on shutdown.
- **Cockpit tab:** a React Flow graph laid out by dagre, left to right in calling order, so each model sits beside its callers. It re-lays out only when a new part or link appears, so nothing moves while you watch.
  - Pulses travel each hop: blue for tx, green for rx, red for failures.
  - A node glows while a call to it is open.
  - A timeline sits beside the graph. "Follow latest", or clicking a notice, lights that request's path and rings the node holding it now.

## M6 requirements (from the owner): the cockpit

A new view in the control panel that shows all communication inside Archangel as it happens. It should look cool: a live graph or chart, not a log. It's a nice-to-have, so it must never slow, block or break the real work.

**What the owner wants to see**
- **Where their request is right now.** Follow one message from the channel it arrived on, through the orchestrator, into the task it started, and back out to whichever channel the answer goes to.
- **Which agent is doing what.** Orchestrator thinking, a tool call, the pilot deciding, the guard checking a tap, Jev or Luna locating, the investigator diagnosing, the phone acting or taking a screenshot.
- **Messages travelling between them.** Each hop between components is visible as it happens: pulses along edges in a graph of the components, with sends and receives (tx/rx) both shown.

**Shape (the owner's suggestion, refined)**
- **Every component announces what it's doing.** It sends a small activity notice: who, to whom, tx or rx, what kind of thing (message, tool call, model call, phone command, screenshot), a short summary, and which request or task it belongs to. Notices are fire-and-forget.
- **One in-process broadcast hub.** It fans notices out to whoever is listening. It's lossy by design: no listeners means notices are dropped, and a slow listener misses notices rather than holding anything up. At most there's a small recent buffer, so a newly opened cockpit isn't empty.
- **A WebSocket on the panel API.** It streams the hub to the browser and is broadcast only, so the browser never sends commands over it. The cockpit draws itself purely from that stream.
- **Correlation.** Notices carry ids (the owner message → the task it started → that task's steps), so the cockpit can highlight one request's whole path and show where it is now.

**Design questions for the plan to settle**
- **Explicit announcements or tracing.** Should each component announce explicitly, or should the hub be fed by tracing that already exists? Pydantic AI emits OpenTelemetry spans for agent runs, model calls and tool calls, and a span listener could cover the model side for free. Choose whichever gives the clearest picture for the least code. The phone, channels and gateway will need their own announcements either way.
- **Where announcing lives.** Ideally a few seams rather than calls scattered everywhere: the gateway, `Event` handling, `logs.run` for tool calls, `Phone._command`, and the locators, guard and investigator.
- **What the cockpit draws.** A node graph with animated edges, plus a running timeline and a "follow this request" mode. Keep the panel's existing simple style: React, plain CSS, and SVG or canvas. A graph library only if it clearly earns its place.

**Not in scope**
- **Not a source of truth.** Tasks, steps and the transcript stay in the store; the cockpit isn't stored or replayed.
- **No control.** Commands don't flow through the cockpit.
- **No remote access.** It's local only, like the rest of the panel.

## Cleanup (done, before M6)

- **One conversation:** one history for the owner across every channel. Messages carry an `Address`; the model sees "via cli". The gateway records one transcript, which the panel shows.
- **Events:** `OwnerMessage` and `TaskUpdate` behind one `Orchestrator.handle(event)`. Each has its own prompt, default reply address, owner authority and failure message.
- **Routing:** replies go where the message came from, and task updates go where the task was asked for. `start_task(report_via=…)` and `send(channel, text)` override that. The panel's buttons are owner messages.
- **Screenshots:** JPEG quality 30 from the bridge (half the bytes; Jev rejects WebP). Stored as files deleted after 3 days. Paused runs keep only the latest screen. `logs/` no longer copies them.
- **Duplication removed:** `models.py` (model building, image conversion, `Instructions`); `Pilot(model, Kit(...))`; one `Pilot` signature; `Screenshot` became `Image`; tasks say `needs approval` / `needs answer` explicitly.

The owner also tests across channels and reports changes.

## M3 (done)

- **Orchestrator** talks and manages; **tasks** run pilots in the background, one on the phone at a time.
- **Human-in-the-loop:** a guard model checks every tap; sends, payments, logins, deletes and setting changes pause until the owner approves that exact tap. Approvals only count in turns the owner triggered. The pilot can also `ask_owner`. Screenshots go to the owner.
- **Escape hatches:** `done` / `give_up`; a nudge after the same failure twice; 5 failures in a row end the task; 40-step cap; `cancel_task`.
- **Durable:** SQLite history (continuous) and tasks; a paused task resumes after a restart.
- **Failures:** the phone gets 60 s to come back online; failed tasks go back to the orchestrator, which retries once or tells the owner.
- **Typing:** the bridge checks the text landed, else pastes.

## M4 (done)

- **Locator fallback:** Jev first; if it can't find the target or fails, Luna (`openai/gpt-6-luna`) looks at the same screenshot.
- **Investigator:** Luna again. When the pilot repeats a step 3 times in 6 (scrolling aside) or fails the same way twice, it reviews the recent steps and last 3 screenshots and says what's in the way. At most 2 per run. Stuck tasks report its diagnosis too.
- **Still open:** a cheap text model (DeepSeek) for the orchestrator. DeepSeek can't see images, and the orchestrator gets approval screenshots, so it needs them stripped first.

## M5 (done)

- **Panel:** `apps/panel`, React + Vite + plain CSS, polling the agent's local API (`channels/panel.py`, 127.0.0.1:8765).
- **Monitoring:** every task with its live screen, steps (each with its screenshot) and report.
- **Channel:** chat with Archangel; approve, decline, answer or cancel waiting tasks in one click.
- **Prompts:** five named prompts, defaults in code, edits in SQLite, live from the next run.
- **Not yet:** images from the panel chat; connectors and MCP setup (M9).

## M5 requirements (from the owner)

- **Local web app, simple.** React with plain CSS; runs locally beside the agent.
- **Monitoring.** Task status, live progress, each run's steps and screenshots.
- **A channel.** Chat with Archangel from it, approvals included.
- **Prompts in the database.** The defaults stay in the code; edited versions are saved in the store and edited from the panel.
- **Setup.** Later: connectors and MCP servers (M9) are configured here.

## M10 requirements (context twin) (from the owner)

- **Learns as it goes.** Standing instructions said in any channel become memories with no extra step. Examples: "never return sponsored results for restaurant recommendations"; "Uber: always the cheapest ride, rate drivers 5 stars, no tip".
- **Recalled before acting.** Before a task, the orchestrator looks up relevant memories (RAG or similar, still undecided) and hands them to the sub-agent that does the work. Example: booking an Uber pulls in the Uber rules.
- **Also skills.** The same store can hold how-tos for tasks, e.g. how to book in a specific app.

Temporal waits for M12: Pydantic AI supports it natively, so it wraps the agent rather than replacing it.