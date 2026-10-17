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
| M6 | Deploy + Telegram | message Archangel from anywhere | new channel, no orchestrator change; agent on Railway |
| M7 | MCP tools | "summarise today's emails" without the phone | MCP toolsets beside phone tools, set up from the panel |
| M8 | Context twin | say "never show me sponsored restaurants" once; every later task obeys it | self-learning memory: rules captured from chat, retrieved before each task |
| M9 | Voice | call your Twilio number | LiveKit SIP as a channel |
| M10 | Autonomy | scheduled and event-triggered tasks | triggers as channels; Temporal if needed |

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
- **Not yet:** images from the panel chat; connectors and MCP setup (M7).

## M5 requirements (from the owner)

- **Local web app, simple.** React with plain CSS; runs locally beside the agent.
- **Monitoring.** Task status, live progress, each run's steps and screenshots.
- **A channel.** Chat with Archangel from it, approvals included.
- **Prompts in the database.** The defaults stay in the code; edited versions are saved in the store and edited from the panel.
- **Setup.** Later: connectors and MCP servers (M7) are configured here.

## M8 requirements (from the owner)

- **Learns as it goes.** Standing instructions said in any channel become memories with no extra step. Examples: "never return sponsored results for restaurant recommendations"; "Uber: always the cheapest ride, rate drivers 5 stars, no tip".
- **Recalled before acting.** Before a task, the orchestrator looks up relevant memories (RAG or similar, still undecided) and hands them to the sub-agent that does the work. Example: booking an Uber pulls in the Uber rules.
- **Also skills.** The same store can hold how-tos for tasks, e.g. how to book in a specific app.

Temporal waits for M10: Pydantic AI supports it natively, so it wraps the agent rather than replacing it.