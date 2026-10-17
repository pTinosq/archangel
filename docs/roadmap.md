# Roadmap

Each milestone ships something usable and proves one idea.

| # | Milestone | You can… | Proves |
|---|---|---|---|
| M0 ✅ | Bridge + relay | drive the phone over HTTP | cable-free phone control |
| M1 ✅ | Comms layer + CLI + brain | chat in the terminal, send images; idle conversations expire | channel abstraction, model-agnostic brain |
| M2 ✅ | Phone hands | "open WhatsApp and read my last message" | phone sub-agent: screenshot → act → verify, retries, step budget |
| M3 | Durable threads + human-in-the-loop | resume after restart; agent asks before risky actions | persisted history, `ask_user` |
| M4 | Deploy + Telegram | message Archangel from anywhere | new channel, no brain change; agent on Railway |
| M5 | MCP tools | "summarise today's emails" without the phone | MCP toolsets beside phone tools |
| M6 | Long-term memory | preferences remembered across weeks | expired threads → memories → `recall` tool |
| M7 | Voice | call your Twilio number | LiveKit SIP as a channel |
| M8 | Autonomy | scheduled and event-triggered tasks | triggers as channels; Temporal if needed |

Temporal waits for M8: Pydantic AI supports it natively, so it wraps the agent rather than replacing it.
