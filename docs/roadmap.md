# Roadmap

Each milestone ships something usable and proves one idea.

| # | Milestone | You can… | Proves |
|---|---|---|---|
| M0 ✅ | Bridge + relay | drive the phone over HTTP | cable-free phone control |
| M1 ✅ | Comms layer + CLI + brain | chat in the terminal, send images; idle conversations expire | channel abstraction, model-agnostic brain |
| M2 ✅ | Phone hands | "open WhatsApp and read my last message" | phone sub-agent: screenshot → act → verify, retries, step budget |
| M3 | Human-in-the-loop + escape hatches + durable threads | agent texts you "Send this? [screenshot]", you reply "yes"; it stops itself when stuck; resumes after restart | `ask_user` round-trip through any channel; stuck detection |
| M4 | Deploy + Telegram | message Archangel from anywhere | new channel, no brain change; agent on Railway |
| M5 | MCP tools | "summarise today's emails" without the phone | MCP toolsets beside phone tools |
| M6 | Context twin | say "never show me sponsored restaurants" once; every later task obeys it | self-learning memory: rules captured from chat, retrieved before each task |
| M7 | Voice | call your Twilio number | LiveKit SIP as a channel |
| M8 | Autonomy | scheduled and event-triggered tasks | triggers as channels; Temporal if needed |

## M3 requirements (from the owner)

- **Human-in-the-loop is upstream as well as downstream.** The agent can ask a question mid-task through whatever channel you're on (CLI now; RCS/SMS/Telegram later). It attaches a screenshot, waits for a reply ("yes", "no", "maybe", or free text), then continues.
- **Mandatory before** sending or posting anything, logging in, payments and purchases, deleting, and changing settings. Prompts alone aren't enough: enforce it in code.
- **Escape hatches.** The pilot must notice it's stuck and stop, or ask, rather than loop. For example: stop after N consecutive failed actions or repeated identical screens, and give it an explicit `give_up(reason)` / `ask_user` route.
- **Seen live (2026-10-17):** drafting a tweet in X looped about 20 times on "Focused field rejected the text". `ACTION_SET_TEXT` doesn't work on X's compose box, so `type_text` needs a fallback (paste via clipboard, or IME input).

## M6 requirements (from the owner)

- **Learns as it goes.** Standing instructions said in any channel become memories with no extra step. Examples: "never return sponsored results for restaurant recommendations"; "Uber: always the cheapest ride, rate drivers 5 stars, no tip".
- **Recalled before acting.** Before a task, the brain looks up relevant memories (RAG or similar, still undecided) and hands them to the sub-agent that does the work. Example: booking an Uber pulls in the Uber rules.
- **Also skills.** The same store can hold how-tos for tasks, e.g. how to book in a specific app.

Temporal waits for M8: Pydantic AI supports it natively, so it wraps the agent rather than replacing it.
