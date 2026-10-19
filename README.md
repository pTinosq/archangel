# Archangel

An AI agent that operates a real Android phone over the network. Roadmap: [docs/roadmap.md](docs/roadmap.md).

## Setup

Needs `just`, `uv`, JDK 17, Android SDK. Phone needs USB debugging.

```sh
cp .env.example .env     # set ARCHANGEL_TOKEN and OPENROUTER_API_KEY
```

## Talk to the agent

```sh
just agent
› hi
› /image ~/Desktop/shot.png what's this?
› what's my battery level?      # uses the phone; needs the relay and bridge running
```

It's one conversation whichever way you talk to it (terminal, control panel), and it can send things to a different channel when you ask. Phone work runs as background tasks, so you can keep talking, ask for status, or say stop. Questions the web can answer don't touch the phone: quick ones are a web search (Tavily, with `TAVILY_API_KEY`), and "look into…" is research (Perplexity Pro Search, a minute or two and a few pence; deep research, ~70p, only after you agree to the cost) that runs beside phone tasks; a reporter agent (Claude) writes it up as a short web page, and you get its link and a tldr. Your email, calendar, Notion and Slack (several accounts each, in profiles like work and personal), once connected, are reached directly (a helper agent, beside phone tasks) rather than through the phone. Before anything that sends, pays, logs in, deletes or changes a setting, it asks you, and prints a screenshot path; reply yes or no. History and tasks live in `apps/agent/data/` and survive restarts; screenshots are kept for 3 days. If Jev can't find something on screen, or a task feels stuck, a second model (GPT-6 Luna) takes a look.

Other model: `ARCHANGEL_MODEL=google/gemini-3.5-flash just agent`. Settings: [CLAUDE.md](CLAUDE.md#agent).

## Control panel

```sh
just agent               # keep running
just panel               # open http://localhost:5173
```

Watch tasks live (screen, steps, report), approve or answer waiting tasks, chat with Archangel, edit the prompts, and connect services (Connectors: Gmail and Calendar, Notion, Slack, any MCP server; sign in again there when one expires). Each page has its own address (`/tasks/12`, `/connectors`). The Cockpit shows requests travelling between the parts of Archangel as it works: which agent is busy, which model it's asking, what the phone is doing. Local, until you deploy it.

## Drive the phone

```sh
just relay               # keep running
just reverse
just bridge-install
```

Open **Archangel Bridge** on the phone. It should say **Connected**. Then, with `Authorization: Bearer $ARCHANGEL_TOKEN`:

```
GET  /devices
POST /devices/<device>/commands  {"type":"tap","x":540,"y":1200}
GET  /devices/<device>/screenshot.jpg
```

Or `just send '{"type":"key","key":"home"}'`. Commands: [docs/protocol.md](docs/protocol.md).

Without the cable: `just tunnel`, then set the app's URL to `wss://<ngrok-host>/ws/device`.

The app keeps two relays, **Dev** (this laptop) and **Prod** (deployed); one tap switches between them.

## Deploy

Archangel on Railway, the phone on your desk, the laptop closed.

1. A Railway project with two services from this repo, `relay` and `agent`, each with a domain. Their settings: [CLAUDE.md](CLAUDE.md#deploy).
2. A GitHub OAuth App with callback `https://<agent-domain>/auth/callback`.
3. Its client ID and secret go in the agent's variables as `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`.
4. On the phone: URL `wss://<relay-domain>/ws/device`, and the relay's token. Keep it charging, on Wi-Fi, unlocked ("Stay awake"), battery optimisation off.

Then open `https://<agent-domain>` and continue with GitHub. Only `ARCHANGEL_OWNER` gets in.

To text or call it: a Twilio number, its webhooks pointed at the agent, and the `TWILIO_*` and `ARCHANGEL_PHONE` variables ([CLAUDE.md](CLAUDE.md#deploy)). Only your mobile gets an answer. On a call it does quick things while you wait; for longer ones it asks whether to ring you back, and otherwise texts. It hangs up when you're done. For "near me" or a pickup it texts you a link that shares where you are, once (the phone stays at home); it types that address into the app.

Details: [CLAUDE.md](CLAUDE.md).
