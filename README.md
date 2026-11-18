# Archangel

An AI agent that operates a real Android phone and a Windows PC over the network. Roadmap: [docs/roadmap.md](docs/roadmap.md).

## Setup

You need `just`, `uv`, JDK 17 and the Android SDK. The phone needs USB debugging.

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

To use another model: `ARCHANGEL_MODEL=google/gemini-3.5-flash just agent`. Other settings: [CLAUDE.md](CLAUDE.md#agent).

### One conversation, kept as threads

It's one conversation whichever way you talk to it (terminal, control panel), and it can send things to a different channel when you ask.

The conversation is kept as threads, one per topic. Each message goes to the thread it carries on, or to a new one, so Archangel answers with that topic in mind ("how much were those flights again?" a week later finds them). A thread you wrap up, or leave for a few hours, is summarised ([docs/threads.md](docs/threads.md)). You can also open a thread yourself from the panel's chat and keep a topic apart from the rest.

### Tasks

Phone and PC work runs as background tasks (one at a time on each), so you can keep talking, ask for status, or say stop.

It does things later too: "in 15 minutes get me an Uber home", "at 20:30 text me to call Sam", "tomorrow morning call me with the weather", "every weekday at 7…". These are kept through restarts. It wakes at the time, in the thread you asked in. They are listed on the Tasks page, where you can cancel them.

### Quick tools

Live questions are answered in seconds, with no phone and no web search: "is the Victoria line running?", "how do I get to Shoreditch?", "when's the next 73?", "will it rain at 5?", "how much is 50 euros?", "what's the time in Tokyo?", "when's the next bank holiday?", or a sum done exactly. They use free sources that need no key (TfL, Open-Meteo, Frankfurter, gov.uk). Archangel tries them first: a tool, then the web, then research, and the phone or PC only for what lives in your accounts and apps, or needs doing. Switch each on or off on the Connectors page, where TfL also takes an optional app key (it only raises the rate limit).

### Web, research and reports

Questions the web can answer don't touch the phone:

- Quick ones are a web search (Tavily, with `TAVILY_API_KEY`).
- "Look into…" is research (Perplexity Pro Search, a minute or two and a few pence). It runs beside phone tasks. Deep research (~60p) only runs after you agree to the cost.
- A reporter agent (Claude) writes the result up as a short web page, and you get its link and a tldr.

Reports are private (you, signed in) until you make one public to share it: ask Archangel, or flip it on the task's page.

### Connected services

Your email, calendar, Notion and Slack (several accounts each, in profiles like work and personal), once connected, are reached directly (by a helper agent, beside phone tasks) rather than through the phone.

### Email (planned, M21)

Archangel will get an email address of its own: write to it or forward it something and it reads it, and it can email people from there. Not built yet; see [docs/roadmap.md](docs/roadmap.md).

### Approvals and history

Before anything that sends, pays, logs in, deletes or changes a setting, it asks you and prints a screenshot path; reply yes or no.

History and tasks live in `apps/agent/data/` and survive restarts. Screenshots are kept for 3 days. If Jev can't find something on screen, or a task feels stuck, a second model (GPT-6 Luna) takes a look.

### What it learns about you

Archangel learns what lasts about you ("I hate morning flights", "Mum is Helen"), not one-offs. Its context twin reads each conversation once it's gone quiet and tells you what it noted. It also briefs each task with what bears on it ("Uber home": cheapest, 5 stars, no tip). "Remember…" and "forget…" work too ([docs/twin.md](docs/twin.md)).

### How it writes

Short and polite to you, informal on Discord and WhatsApp, semi-formal in email and work chat, plain anywhere else. Each is a small file in `apps/agent/src/agent/styles/` (who it is for, then the style in prose) over a shared base: plain words, no padding, each thing said once. A style can be for a group of your people (`groups = ["uni friends"]`): tell Archangel "Sam and Priya are my uni lot" and the twin remembers who's in it, so they get that style wherever you message them. Adding one is adding a file; `just style Sam discord` shows which one an audience gets. Styles that name real people go in `apps/agent/data/styles/` on the volume, not the repo ([docs/styles.md](docs/styles.md)).

### Improving itself

"Add a Telegram channel" or "why does the weather call ramble?" goes to Claude Code in GitHub Actions, on your Claude subscription. It comes back as a PR to merge ("merge it" deploys it) or as an answer. Only you can ask, or merge.

## Control panel

```sh
just agent               # keep running
just panel               # open http://localhost:5173
```

The panel is local until you deploy it. In it you can:

- Watch tasks live (screen, steps, report), and approve or answer waiting tasks.
- Chat with Archangel.
- Look back through its threads (Threads: each topic's summary, tasks and what was said; the chat marks where it moves thread).
- Keep concurrent work apart: the chat's dropdown switches between Main (everything, as Archangel sorts it) and the open threads. Archangel opens a thread for each new topic; what you say in one you've chosen stays in it; Archangel closes threads when they're done.
- Edit the prompts.
- See and shape what Archangel has learned about you (Context twin: a graph of linked pages; add nodes, drag to link them, edit any of it).
- Connect services (Connectors: Gmail and Calendar, Notion, Slack, any MCP server; sign in again there when one expires), and switch the quick tools on and off.

Each page has its own address (`/tasks/12`, `/connectors`). Small sounds play for presses, messages, approvals and the cockpit's traffic; "Sound off" in the header silences them.

The Cockpit shows requests travelling between the parts of Archangel as it works: which agent is busy, which model it's asking, what the phone is doing, and which pages of your context twin it reads or edits.

The Architecture tab is a flowchart of how Archangel handles anything it's told, every decision on the way; click a part for what it does, or play a scenario (a thanks in a thread, a question on a call, an Uber in 15 minutes) through it a step at a time.

The twin has its own evals on a real model: `just twin-eval`, or the panel's Evals tab when running locally, to run them and see each run's graph (and `just twin` to try it by hand). The thread router's evals are `just threads-eval`.

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

## Drive a Windows PC

Download `archangel-bridge.zip` from the [Windows bridge release](https://github.com/pTinosq/archangel/releases/tag/bridge-windows), unzip it, run `archangel-bridge.exe`, and enter the relay's URL and token, a name, and the PC's PIN (so it can unlock the lock screen). **Save and start** installs it as a service, running from boot. Details, and waking a sleeping PC from the phone: [apps/bridge-windows/README.md](apps/bridge-windows/README.md).

Then ask Archangel to do something "on my PC", or run `just pc-send '{"type":"key","key":"win"}'` and `just pc-screenshot`. The protocol is the same as the phone's, with a few commands of its own (mouse wheel, unlock).

## Deploy

Archangel on Railway, the phone on your desk, the laptop closed.

1. Create a Railway project with two services from this repo, `relay` and `agent`, each with a domain. Their settings: [CLAUDE.md](CLAUDE.md#deploy).
2. Create a GitHub OAuth App with callback `https://<agent-domain>/auth/callback`.
3. Put its client ID and secret in the agent's variables as `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`.
4. On the phone, set the URL to `wss://<relay-domain>/ws/device` and enter the relay's token. Keep it charging, on Wi-Fi, unlocked ("Stay awake"), with battery optimisation off. For "near me" in apps that only use GPS: Developer options → Select mock location app → Archangel Bridge.

Then open `https://<agent-domain>` and continue with GitHub. Only `ARCHANGEL_OWNER` gets in.

### Texts and calls

You need a Twilio number, its webhooks pointed at the agent, and the `TWILIO_*` and `ARCHANGEL_PHONE` variables ([CLAUDE.md](CLAUDE.md#deploy)). Only your mobile gets an answer.

On a call it does quick things while you wait. For longer ones it asks whether to ring you back, and otherwise texts. It hangs up when you're done.

For "near me" or a pickup it texts you a link that shares where you are, once (the phone stays at home). It types that address into the app.

### Self-improvement

1. Run `claude setup-token` and save the token as the repo secret `CLAUDE_CODE_OAUTH_TOKEN` (`gh secret set CLAUDE_CODE_OAUTH_TOKEN`, in your own terminal).
2. Let Actions open PRs (Settings → Actions → General).
3. Give the agent `GITHUB_TOKEN`, a fine-grained token for this repo only (Actions, Contents, Pull requests: read and write).

Details: [CLAUDE.md](CLAUDE.md).
