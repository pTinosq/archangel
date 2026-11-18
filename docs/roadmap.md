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
| M7 ✅ | Deploy | use Archangel from anywhere with the laptop closed: the agent and relay on Railway, the phone on the desk, the panel behind "Continue with GitHub" | runs detached from localhost; one owner, no auth platform |
| M8 ✅ | Twilio | text, then call, your Twilio number to talk to Archangel; it texts or rings you for approvals | SMS, then voice, as channels; no orchestrator change |
| M9 ✅ | Research | "do deep research into the best funds in 2026": a cited report a minute or two later | research beside phone tasks, not on the phone; the orchestrator picks web or app |
| M10 ✅ | Location | "restaurants near me" and "get me an Uber" from anywhere: the phone at home acts as if it's where you are | your location on request, typed where an app takes an address, faked only where it doesn't |
| M11 ✅ | Connectors | "summarise today's emails" without the phone | MCP servers as a helper's tools, added and signed in to from the panel |
| M12 ✅ | Context twin | say "never show me sponsored restaurants" once; every later task obeys it | a second brain that grows: a graph of linked pages, written from what the owner says, walked before each task ([design](twin.md)) |
| M13 ✅ | Autonomy | "in 15 minutes get me an Uber home", "every weekday at 7 call me with the weather"; see and cancel them on the panel | time as a trigger: scheduled and repeating work wakes the orchestrator in the thread it was asked in; rows and a sleeping loop, no Temporal |
| M14 ✅ | Self-improvement | "add a Telegram channel" or "find out why Notion won't connect" by text | a coding agent works on Archangel's own repo; the owner approves every merge |
| M15 ✅ | Intelligent tools | "is the Victoria line running?", "will it rain at 5?" answered in seconds, from anywhere | live data as quick tools (TfL, weather, more as they earn a place), beside the web: no phone, no browsing |
| M16 | Pebble Time 2 | talk to Archangel from the watch: dictate, read the answer, approve with a button | the wrist as a channel; one conversation across watch, texts, calls and panel |
| M17 ✅ | Windows PC | "do X on my PC" from anywhere, the PC unattended at home, even locked | a second device behind the same relay and pilot; Windows' lock screen and UAC passed without the model seeing a password |
| M17a ✅ | PC hands | tasks on an unlocked, auto-signed-in PC, beside phone tasks | a Windows bridge (a service with a settings window) speaks the same protocol; Jev finds things on desktop screens |
| M17b ✅ | Unlock | tasks on a locked PC, through UAC prompts | a SYSTEM service reaches the secure desktop; the PIN stays on the PC |
| M17c ✅ | Wake | tasks on a sleeping PC | the phone on the home Wi-Fi sends Wake-on-LAN |
| M18 ✅ | Threads | "how much were those India flights again?" a week later, and it picks up that conversation; unrelated asks start clean | one conversation for the owner, many threads inside: a small quick model routes each message to a thread, so the orchestrator carries only what's relevant |
| M18a ✅ | Choosing threads | two unrelated pieces of work side by side without tangling one feed | the panel's chat gets a dropdown: Main or an open thread; what's said in one goes to it unrouted |
| M19 | Files | hand it a PDF, an image or a document and have it kept, read and worked on; "draw me a logo" and the picture is saved; "write a script that…" and the file is on disk | a scoped filesystem for the agent: the owner's files in, its own outputs and code out, never outside its folder |
| M20 ✅ | Flavour profiles | "text Sam like I would" comes out in the owner's voice; the same news reads short and polite to the owner, looser on Discord, semi-formal to a stranger | writing styles as configuration: one profile per audience, channel or app, each written and tested on its own, over a shared no-slop base ([design](styles.md)) |
| M21 | Email gateway | email Archangel at its own address, or forward it a message, and it reads it; "email Sam the details" sends from that address | email as a channel: a dedicated inbox in, outbound mail out, one conversation with the owner's other channels |
| Later | Twin upkeep | the twin tidies itself overnight and keeps how-tos | nightly tidying (merges, tendencies, fading), skills, search as an entry point |
| Later | Telegram | message Archangel from Telegram | another channel |
| Later | Notification triggers | "when my Uber arrives, text me" | phone notifications as triggers (allow-listed apps only); parked from M13 for the loop and model calls it costs |

## M9 requirements (research) (from the owner)

- **Deep, not a search.** Many sources, read and cross-checked, ending in a report with citations. Good, not exhaustive (the owner, after trying deep research).
- **Cheap.** Cents per report, not dollars.
- **Runs in the background.** Started from any channel like any task, and the phone stays free meanwhile. The report goes where it was asked for: short on sms and voice, in full on the panel.
- **The right source.** The phone has apps with their own content (the Michelin Guide). Archangel decides between the web and an app, and the owner can say which they prefer.

**Plan**
- **Engine: Perplexity Pro Search via OpenRouter** (`perplexity/sonar-pro-search`): about 30 seconds and 3p, a direct answer with tables and ~15 sources. Sonar Deep Research (~4 minutes, ~60p, an 80k-character essay whatever it's told) stays for exhaustive asks: offered with its cost, run only once the owner agrees. A research task never touches the phone, so it runs beside phone tasks.
- **Upgrade: GPT-5.6 Sol with web search** for "really dig into this", at roughly 3–5× the cost. OpenAI's own deep research models were shut down on 2026-07-23.
- **Web or app.** The orchestrator sees the phone's installed apps (the bridge's `apps` command, today only the pilot uses it). The rule of thumb: general knowledge comes from the web; the owner's own accounts, app-only content and anything to act on (booking, ordering) use the phone. It can chain them: research the best Michelin restaurants nearby, then book one in the app. It asks when unsure. A stated preference ("restaurants: use the Michelin Guide") becomes a standing rule in M12.
- **Quick questions** ("what time does Tesco close?") get a plain web search (Tavily), not deep research.
- **A page to read on the phone.** The reporter (the main model) writes what research found as a short HTML page; the owner gets its link and a tldr.
- **Shareable (added 2026-10-20).** Reports are private (the owner, signed in) by default; the owner makes one public to send to someone, by asking or from the panel, and can ask which it is.
- **Not the ChatGPT app:** it's free, but has no clear "done" signal, its layout can change, and a long report is hard to get off the screen. Phone notifications as a wake-up signal belong to M13.

## M11 requirements (connectors) (from the owner)

- **Gmail, Google Calendar, Notion and Slack** first, and any other MCP server.
- **A Connectors page** in the panel: choose a service, set it up, and sign in again when one expires.
- **Several accounts**, e.g. work and personal Google, Notion, Slack, grouped in **profiles**; Archangel knows which one is meant.

**Plan**
- **A helper agent** does connector tasks in the background, beside the phone: one task, the connected services' tools, an answer. The orchestrator prefers it to the phone for anything connected.
- **Read freely, ask before acting.** Tools a server marks read-only run at once; anything else (send, write, delete) pauses for the owner's approval, like a consequential tap.
- **Notion** is Notion's own server: one sign-in. **Slack**'s own server needs the owner's internal Slack app (client ID and secret, entered once). **Gmail and Calendar**: Google's servers are invite-only, so Archangel runs workspace-mcp itself on its own Google sign-in (the owner's Google Cloud OAuth client, entered once).
- **Expired sign-ins** show on the page as "needs sign-in"; a task that can't reach a service says so.

## M10 requirements (location) (from the owner)

- **Near me means near the owner.** "Near me" searches (Maps, the Michelin Guide) and Uber pickups use where the owner is, not where the phone sits at home.
- **Easy on the battery.** Nothing on the owner's own phone (Android) runs in the background.

**Plan**
- **Ask when needed.** When a task needs the owner's location, Archangel texts a link; one tap shares it once from the browser. Saying a place ("outside Liverpool Street") works too, on a call or in a text. A recent location is reused for a short while instead of asking again.
- **Type before faking.** Apps with an address field (Uber, Deliveroo, Maps directions) get the address typed. This is safer because Uber and banking apps can spot a faked location and may flag the account.
- **Fake only for GPS-only apps.** The bridge becomes the phone's mock location app (Developer options, set once). The phone moves to the owner's location for that task, then goes back home. A new bridge command, `docs/protocol.md` first.
- **Check it took.** Some apps read Wi-Fi and cell location too; the pilot checks the results look local before trusting them.
- **Later, if the link gets tedious:** always-on Google Maps location sharing with the home phone's account.

**Progress**
- **Sharing (done):** `owner_location` gives the last place and a signed link; its page shares the browser's location once and the orchestrator carries on. Addresses come from OpenStreetMap. The pilot types the place where an app takes one.
- **Faking (done):** the bridge's `location` command (mock location app); the pilot's `set_location`, only for GPS-only apps, put back when its run ends or pauses. It checks the results look local.
- **Tried live (2026-10-24):** the faked location moves Maps' dot and its "near me" results. Still to see: the pilot choosing it itself, from a texted "restaurants near me", and an Uber pickup typed.

## M8 (done)

- **SMS (done, live):** `channels/twilio.py`. Twilio posts the owner's texts to `/twilio/sms`; each is checked against Twilio's signature, and other numbers are ignored. Replies go by Twilio's API. Screenshots are a link to the panel (UK numbers can't send pictures). `web.py` is now the one server every HTTP channel adds routes to.
- **Voice (done, live):** ConversationRelay. Twilio does speech and turn-taking; Archangel trades text over a WebSocket. Quick things are done while you wait; it rings back only when asked, and a voicemail gets the message after the beep, then the orchestrator decides what to text. On a call, screenshots are texted as a link. Caller ID is trusted (the owner's choice).

## M7 (done)

- **Railway project `archangel`:** `relay` and `agent` services, deployed from `main` on push. Settings live on the services; Railway deprecated `railway.json`. The agent's Dockerfile builds the panel and serves it beside the API: one origin. A volume keeps `data/`.
- **Sign-in:** a GitHub OAuth App used directly (`channels/login.py`), no auth platform. Only `ARCHANGEL_OWNER` gets in; a signed session cookie lasts 30 days. The panel only listens publicly when sign-in is configured, so it can't be public by mistake.
- **Headless:** no terminal on Railway, so the panel is the only channel.
- **Phone:** the bridge keeps a Dev and a Prod relay; one tap switches.
- **Closed (2026-11-18):** the cockpit once went blank in the cloud after a phone task started; never seen again.

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
- **Investigator:** Luna again. When the pilot repeats a step 3 times in 6 (scrolling aside) or fails the same way twice, it reviews the recent steps and last 3 screenshots and says what's in the way. At most 2 per run. Stuck tasks report its diagnosis too. (Since M8: no loop rules; the pilot calls `second_opinion` when it judges it's going in circles, with 120 steps only as a backstop.)
- **Done:** a cheap model. DeepSeek V4.1 Flash now sees images, so it runs everything in prod (`ARCHANGEL_MODEL`), the pilots and guard included.

## M5 (done)

- **Panel:** `apps/panel`, React + Vite + plain CSS, polling the agent's local API (`channels/panel.py`, 127.0.0.1:8765).
- **Monitoring:** every task with its live screen, steps (each with its screenshot) and report.
- **Channel:** chat with Archangel; approve, decline, answer or cancel waiting tasks in one click.
- **Prompts:** five named prompts, defaults in code, edits in SQLite, live from the next run.
- **Images:** attached in the chat (chosen, pasted or dropped), up to 4 a message.
- **Not yet:** connectors and MCP setup (M11).

## M5 requirements (from the owner)

- **Local web app, simple.** React with plain CSS; runs locally beside the agent.
- **Monitoring.** Task status, live progress, each run's steps and screenshots.
- **A channel.** Chat with Archangel from it, approvals included.
- **Prompts in the database.** The defaults stay in the code; edited versions are saved in the store and edited from the panel.
- **Setup.** Later: connectors and MCP servers (M11) are configured here.

## M12 requirements (context twin) (from the owner)

- **Learns as it goes.** Standing instructions said in any channel become memories with no extra step. Examples: "never return sponsored results for restaurant recommendations"; "Uber: always the cheapest ride, rate drivers 5 stars, no tip".
- **Recalled before acting.** Before a task, the orchestrator looks up relevant memories (RAG or similar, still undecided) and hands them to the sub-agent that does the work. Example: booking an Uber pulls in the Uber rules.
- **Also skills.** The same store can hold how-tos for tasks, e.g. how to book in a specific app.
- **Design (2026-10-24):** `docs/twin.md`. Not a table of memories but a graph of pages with previewed links, walked by a reader and grown by a writer that usually keeps nothing; one-offs stay out. Visible as a Twin tab in the panel.
- **Progress (2026-10-24):** graph, writer, reader and the Context twin tab built and deployed; the owner's graph seeded from a questionnaire (kept out of the repo). Still to see live: a briefing changing what a task does; the writer's "Noted".
- **Done (2026-11-15).** The owner judged it good in use. Tidying, skills and search moved to "Twin upkeep" (Later).

## M13 requirements (autonomy) (from the owner)

- **Later, said naturally (2026-11-17).** "In 15 minutes get me an Uber home" (waits, then orders: Uber's own scheduled rides cost far more), "at 20:30 text me to do this", "tomorrow morning call me and tell me the weather" (it works out what tomorrow morning means).
- **Then event-triggered:** "when my Uber arrives, text me" (phone notifications, allow-listed apps only).

**Plan**
- **No Temporal.** One owner's timers are rows in the store and one sleeping loop; Temporal is a server to run and pay for. Pydantic AI's Temporal support stays an option for durable runs if they ever need it.
- **Stored absolute, shown both ways.** The orchestrator's `do_later` takes `at` (the owner's local time) or `in_minutes`; code does the arithmetic and shows "Wed 18 Nov 08:00, in 11 h". The orchestrator sees the time every turn, and what's scheduled.
- **Woken, not replayed.** When due, a `Scheduled` event wakes the orchestrator with the owner's words and what it said it'd do, in the thread it was asked in; it then starts the task, texts or rings (`report_via voice`), as if asked now.
- **Never twice.** Marked fired before it runs: a crash loses it rather than ordering two Ubers. Anything due while Archangel was down fires on start, saying how late; the orchestrator judges whether it still makes sense.
- **Repeating** ("every weekday at 7"): a cron expression in the owner's time (models write cron well; cronsim gets daylight saving right). Missed while down: fires once, not once per miss.
- **Seen on the panel:** what's scheduled, soonest first, above the tasks; Cancel sends a message like the task buttons.
- **The agent ends its own loops (2026-11-18).** "Check my email every 30 minutes until it changes, then text me": a repeat's wake-up tells the orchestrator its run number and that `cancel_later` stops it for good once what it waited for has happened. On its own it may cancel only what was scheduled in its own thread; the owner may cancel anything. Every repeat has a cap on runs (`max_runs`, default 50, at most 200: each run is a model call); the run that uses the last is told it was the last, so the owner hears it stopped. Until then its runs, and the tasks they start, tell the owner nothing (`stay_quiet`): asked to check every 15 minutes, it texts once, when there's news. Not its own milestone: a part of M13.
- **Silent until done, with progress (2026-11-18).** A repeat's runs say nothing to the owner, in code, whatever the model writes (a "still 3 of 7" had been texted); only the run that ends it (`cancel_later` on its own job) and its last run reach them. `note_progress` in a run sets a line the Scheduled list shows.
- **Repeats remember (2026-11-18).** A repeat's wake-up carries its last run's task (goal, status, report) and noted progress: it can tell nothing's new, and tells a new pilot the way the last one went instead of it working the route out again. `do_later` points out the same thing already scheduled in the thread (one had been set up twice and fired twice a run).
- **Done (2026-11-17):** scheduled, repeating, on the panel.
- **Notification triggers, parked (2026-11-17)** as their own "Later" milestone: the owner judged them not worth it for now: watching for "when my Uber arrives" means a loop and model calls on every notification.

## M14 requirements (self-improvement) (from the owner)

- **Builds its own features.** Ask from any channel ("add a Telegram channel", "make the panel show costs"); a coding agent works on this repo the way Claude Code does locally, following `CLAUDE.md`.
- **Investigate a bug from anywhere (added 2026-10-23).** The owner sees something wrong ("Notion sign-in says invalid state") and says so from any channel; a coding agent investigates Archangel's code and logs, reports the cause, and proposes the fix as a PR. Investigating is free to start; changing code is a PR the owner reviews.

**Built (2026-11-17)**
- **On the owner's Claude subscription, in GitHub Actions** (`.github/workflows/improve.yml`): no API tokens, and Actions are free on a public repo. The orchestrator's `improve(request, pr?)` starts the workflow; a `code` task follows its run and reports the PR (or the answer to a question) like any task. `pr` carries on a PR ("no, make it shorter").
- **The token stays put.** The Claude token is a repo secret, only in the job that makes the change, which can't write to the repo (its subprocesses don't see the token). A second job refuses a change or summary containing it, then tests, pushes and opens the PR. Archangel holds only a GitHub token for this repo.
- **Only the owner.** `improve` and `merge` refuse anything not from the owner's own message (a web page, an email, a task's update), in code. Merging to `main` deploys, so the owner always says when.
- **Public.** The request, the run and the PR are public: the orchestrator words the request with nothing personal, and the coding agent follows `CLAUDE.md`. So no prod logs or twin go to it yet: investigating is from the code only.
- **Survives its own deploy (2026-11-18).** Merging redeploys Archangel and cuts off what's running, so `merge` lists those tasks for the owner. A code task keeps its GitHub run's id and, after a restart, follows that run to its result rather than being reported interrupted (which had the owner ask again, for a duplicate PR).
- **Says why a run failed.** The workflow always leaves a `result`, with Claude's error when it failed. A run Claude never started in (zero turns: a usage limit or an outage) is tried once more a minute later.
- **Working on the code, not healing (2026-11-17).** M14 is the owner changing Archangel from inside it. Nothing watches for failures and proposes fixes on its own, and that isn't wanted.

## M15 requirements (intelligent tools) (from the owner)

- **Quick, reliable answers from live data**, without the phone or a web search: a set of tools the orchestrator calls inline, in seconds.
- **TfL first:** live tube status (and likely buses, Overground, Elizabeth line): whether a line is running, and why not. Already built in `../pebble-assistant` (pebble-tino): `apps/server/src/tools/tfl.ts`, on TfL's open API (`/Line/Mode/tube/Status`, `/Line/{id}/Status`; `TFL_APP_KEY` optional, raises rate limits). Port it rather than start over.
- **Weather:** now and the next few days, wherever the owner is (or names).
- **Chosen by the owner (2026-11-17):**
  - **TfL journeys and buses:** routes ("how do I get to Shoreditch?") and bus arrivals at a stop (TfL's journey planner and arrivals).
  - **Flight status:** on time or not, gate, landing time (free sources are thin; may need a paid one).
  - **Currency:** live exchange rates (Frankfurter, free).
  - **Time, sun and holidays:** the time anywhere, sunrise and sunset, UK bank holidays (gov.uk), and exact calculations rather than the model's arithmetic.
  - **Markets:** stock, fund and crypto prices and how they've moved.
  - **News:** headlines, or the latest on a topic (BBC, Guardian feeds), quicker than a web search.
  - **Wikipedia and dictionary:** quick facts, definitions and translations, cited.
  - **Places:** open now?, rating, the walk there (Google Places: a key, a free monthly allowance, then paid).
- **More as they earn a place:** anything asked often that the phone or the web answers slowly or unreliably. Passed on for now: National Rail, Santander bikes, air quality and pollen, parcel tracking, sports, food hygiene ratings, bin days, postcodes.

**Built (2026-11-17)**
- **Built-in tools, not MCP connectors.** Each is a `Tool` (`agent/tools/`): a few async functions the orchestrator calls inline, like `search_web`, seconds and no task. Adding one is a subclass listed in `BUILT_IN`; it appears on the panel by itself. They show on the Connectors page (a "Quick tools" column) beside the connectors, but aren't connectors: nothing to sign in to, and the owner's on/off and settings live in the store's `tools` table.
- **First four:** TfL (line status and why, journeys with unclear places resolved to the best match and said, next buses and trains at a stop), weather (Open-Meteo: now, by the hour, the days, sunrise and sunset), exchange rates (Frankfurter) and time and dates (the time anywhere, UK bank holidays, days between dates, exact arithmetic). All free and keyless: no new env vars. TfL takes an optional app key from the panel (rate limit only).
- **Routing.** The orchestrator is told each turn which tools are on and the order to try things: a quick tool, `search_web`, research, and only then a device. A tool that's off, or lacks a setting it requires, isn't offered at all, so the model can't pick it. A tool whose source is down answers "isn't answering: try the web", and the conversation goes on.
- **Where they are:** weather and journeys "from here" use `owner_location` first.
- **Not planned (2026-11-18):** flight status, markets, news, Wikipedia and dictionary. The helper and device pilots don't get the quick tools: the orchestrator answers these itself and puts what a task needs in its goal.

## M16 requirements (Pebble Time 2) (from the owner)

- **A new channel: the owner's Pebble Time 2.** Dictate on the wrist, the answer comes back to the watch, like `../pebble-assistant` (pebble-tino) does today with its own backend.
- **One conversation.** What's said on the watch is the same conversation as texts, calls and the panel; results can come back to the watch.

**Plan (sketch, undecided)**
- **Reuse pebble-tino's watch app.** Watch → phone (PebbleKit JS) → HTTP; point it at Archangel instead of its own server, as a `Channel` with its own route, signed like Twilio's.
- **Short and buttoned.** Replies sized for a watch screen; approvals (yes or no) on the watch's buttons; a buzz when a task needs the owner.

## M17 requirements (Windows PC) (from the owner)

- **The phone setup, for the owner's Windows PC.** Same agent, pilot and Jev; the PC is another device.
- **Logs in too** if possible: a locked PC shouldn't stop a task.

**Plan (sketch, 2026-11-15)**
- **First: does Jev work on desktop screens?** Denser than a phone's, small text; try it on real screenshots before building. Screenshot size and quality need retuning (half-scale at 30 suits a phone, likely not a 1440p monitor).
- **M17a, PC hands.** A Windows bridge in C#/.NET (a tray app) dials the relay like the Android one: `SendInput` for clicks (right, double, drag) and hotkeys, UI Automation for the tree, Desktop Duplication for screenshots. `hello` gains a `platform`; the protocol grows only what a desktop needs. The PC signs in at boot (Sysinternals Autologon) and never locks, like the phone's no-lock setup. `start_task` names a device; one task per device at a time, so phone and PC tasks run side by side.
- **M17b, unlock.** Windows walls off the lock screen, Ctrl+Alt+Del and UAC on a secure desktop no ordinary app can see or click, and blocks input to elevated windows. A service running as SYSTEM starts a helper on that desktop, as TeamViewer, AnyDesk and RustDesk (open source) do. An `unlock` command types the PIN, kept by the bridge on the PC: the model never sees it, as with the phone's `press unlock`.
- **M17c, wake.** A sleeping PC can't dial the relay. The Android bridge, on the same Wi-Fi, sends a Wake-on-LAN packet; or the PC never sleeps.
- **Commands (2026-11-18).** A `powershell` command runs a script as the signed-in user (never SYSTEM), stopped with all it started after 10 s; the pilot's `run_command` opens apps, folders, files and pages, and finds and reads files, far faster than clicking. The guard checks each command by what it does, without the screen: reading and opening run at once; changing files, installing, settings, sending, paying or logging in wait for the owner, who sees the command.
- **Built (2026-11-15), all three; proven on the owner's PC (2026-11-17).** One protocol for every bridge (`platform` in the hello; shared commands keep their names). The Windows bridge in Python rather than C#: the same stack as the relay and agent, most of it tested anywhere, and CI on a Windows runner tests the rest on a real desktop (capture, input, UI Automation, the Start menu) and the built exe as a real service. CI proves the service on a real Windows desktop: it starts the bridge as SYSTEM in the desktop's session, captures the lock screen, and unlocks it with the bridge's secret. Jev on desktop screens: not yet measured. M17a proven on the owner's PC (2026-11-17): Notepad, and Steam (find games, install them). M17b too: a task started on the locked PC unlocked it, ran, and locked it again. M17c too: a task sent to the sleeping PC had the phone wake it (back in 12 s), then unlocked it and ran.

## M18 requirements (threads) (from the owner)

- **Context bloat.** Today the orchestrator sees the last 40 messages, whatever they're about (`history.py`), so an old topic falls out and unrelated ones crowd in.
- **Threads, like folders of discussion.** A small, fast, cheap model in front of the orchestrator puts each message in a thread: a new one when it's unrelated ("text Sam happy birthday" after "flights to India"), or back to an old one ("for the India flights, how much were they?").
- **Finding the thread.** It reads the summaries of the last few threads; nothing likely, or the owner says "we discussed X before": a search of all threads (by word, "India"). The most likely wins: last week's India thread over 2025's.
- **Each thread knows its size** (context length), so a full one is summarised or continued in a new one rather than overfilled.
- **A summary once it's done.** Written when the thread is finished, not after every message. The owner closing it ("thanks", "thx") is the signal.

**Plan (sketch, undecided)**
- **The router judges, no rules:** whether "thanks" closes a thread, or a quiet thread is finished, is the router's call from context, not a keyword list.
- **One owner, any channel:** a thread spans channels (asked by text, answered on the panel); a call is usually its own thread.
- **Tasks belong to the thread that started them**, so their updates come back into it.
- **Beside the twin, not instead of it:** the twin keeps what's lasting about the owner; threads keep what was said and done.
- **Built and live (2026-11-17).** As planned, with: the thread the conversation is in shown to the router apart from the rest; a thread full at 30k tokens carried on in a new one from its summary; quiet threads (3 h) summarised too; the old single history adopted as the first thread. Router eval 85/85 on the real model. Design: [threads.md](threads.md). To tick: used for a while.

## M19 requirements (files and storage) (from the owner)

Today Archangel has no place to keep a file: chat pictures and screens are short-lived images, and a report is one page of HTML. This gives it a folder of its own, scoped so nothing outside it can be read or written.

- **The owner hands it files.** PDFs, images and other documents, from any channel that can carry them (the panel, texts where the carrier allows), kept until deleted.
- **It stores, reads and works with them.** List, open, search and summarise what's there; a PDF's text, an image looked at, a document answered from, a new file made from an old one.
- **It keeps what it generates.** Images from image-generation models are saved rather than shown once and lost, and can be sent back on any channel that shows pictures.
- **It writes code to disk.** Scripts and other code files it makes are written as files the owner can open, download or hand to a later task, not left in a chat message.
- **Scoped.** One storage root, paths checked in code so nothing escapes it (no `..`, no symlinks out); size limits; deleting needs the owner's word.
- **On the panel.** A Files page to browse, upload, download and delete.

**Plan (sketch, undecided)**
- **A `files` tool set for the orchestrator**, beside the quick tools, over a directory on the agent's volume; the store records each file's name, type, size, where it came from and the thread it belongs to.
- **Tasks can be given files**, so a phone or PC task can be handed a document, and what a task produces is filed in the same place.
- **Not decided:** whether the twin links to files, how long generated files are kept, and which image-generation model to use.

## M20 requirements (flavour profiles) (from the owner)

Today how Archangel writes lives in the orchestrator's prompt, one voice for everyone. This makes style a thing of its own: how it addresses the owner, and how it writes on the owner's behalf, differ by audience.

- **Per audience, channel or app.** Short and polite when messaging the owner; more informal on Discord; semi-formal elsewhere. Writing for the owner (a message to Sam, a post, an email) is a different style from talking to the owner.
- **Held as configuration, not in the prompt.** Each profile is its own file or entry, so it can be written, tuned and tested on its own, without touching the orchestrator's prompt or another profile.
- **A shared base profile.** Anti-slop rules every profile inherits: plain words, no AI padding, say each thing once. A profile adds to the base and may relax a rule, never drops the base by accident.
- **Picked by channel, person or app.** A message to the owner by text uses the owner's profile; to a named person, theirs; in an app, the app's. Nothing matches: a sensible default (semi-formal).
- **Extensible.** A new profile is a small addition: a name, what it matches, a few lines of style. No code change to add one.

**Built (2026-11-18, PR #11).** As files: `agent/styles/*.md`, TOML settings saying who each is for (the owner, people, groups, an app), then the style as prose; chosen in code. Who's in which of the owner's groups is the twin's: a person linked to the group's node. Design, selection rules and how to add one: [styles.md](styles.md). Live; the starter wording is still to be tuned against real replies.
- **Finished (2026-11-18).** Each style's text is edited on the Prompts tab ("Writing styles"), its file the default; `just styles-eval` drafts for made-up people and checks them (41/45 first run); the owner can ask for one message to sound different ("make it casual"), passed to `writing_style` as `asked`, for that message only.

## M21 requirements (email gateway) (from the owner)

A new channel, email, with an inbox and address of its own. Not the owner's Gmail connector (M11): that is the owner's mail, reached by tools; this is Archangel's.

- **Its own address.** People can write to it, and the owner can forward things to it.
- **Sends.** It emails people from that address when asked ("email Sam the details"), in the style for that audience (M20).
- **Reads.** Mail the owner sends or forwards to it arrives as an event and is routed into the owner's conversation, in the right thread, like a text.

**Built (2026-11-18), not yet tried live.** Resend for both ways (free: 3,000 emails a month, 100 a day, received ones included), at `archangel@tinos.xyz` (the domain receives no other mail). Detail: `docs/agent.md#channels`.

- **In:** Resend's webhook, then its API for the email. The owner's mail is proved by Resend's DKIM or DMARC result on receipt; anything else is a `Notice`.
- **Threads:** each email kept by its Message-ID; a reply in the chain goes back to its thread, and the answer goes back in the chain.
- **Out to others:** the helper's `send_email`, after the owner's OK with the draft; the orchestrator uses `use_connectors`. Always asked for now: no one is exempt.
- **Attachments:** the owner's pictures to the model; other files named (M19 reads them).

**Plan (as sketched before it was built)**
- **A `Channel` subclass** registered in `__main__.py`: `send` out, inbound mail through `start(deliver)`, with its own `Address` kind; `shows` says what an email can carry.
- **Only the owner's mail is a message.** Mail proved to be from the owner becomes an `OwnerMessage`; anything else is a `Notice` the orchestrator judges, never an instruction: an email can't approve, `improve` or `merge`.
- **Outbound is consequential.** Mail to someone else goes through the owner's approval, unless they've said otherwise for that person.
- **Not decided:** the inbox provider and the address (a domain it can use, set up by the owner), how inbound arrives (webhook or polling), how the sender is proved (SPF/DKIM results, a signed token, an allow-list), how replies thread, attachments, and cost. Credentials will be env vars on Railway, never in the repo.
