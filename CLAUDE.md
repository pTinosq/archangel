# Archangel

AI agent that operates a real Android phone. No cable, ADB or root at runtime. Progress: `docs/roadmap.md`.

```
channels ⇄ gateway ⇄ orchestrator ──▶ tasks ──▶ pilot ──▶ relay ◀──WebSocket── bridge (phone)
 (any medium,  (events in,  (one conversation,  (background     (action)
  one owner)    routing out)  talks, manages)    sub-agents)
```

## Layout

| Path | What |
|---|---|
| `apps/agent/` | Python, Pydantic AI + uv. Channels, gateway, orchestrator, tasks, phone pilot. |
| `apps/bridge/` | Android app, Kotlin. AccessibilityService that dials the relay and runs commands. |
| `apps/relay/` | Python relay, FastAPI + uv. Holds phone connections, exposes the HTTP API. |
| `apps/panel/` | Control panel, React + Vite + plain CSS. Polls the agent's API every second; the Cockpit tab draws the live activity stream with React Flow, laid out by dagre from who calls whom. |
| `docs/protocol.md` | Phone wire contract. Change it first, then bridge and relay. |
| `docs/roadmap.md` | Milestones. Tick them off as they land. |
| `justfile` | All dev tasks. `just` lists them. |
| `.env` | `ARCHANGEL_TOKEN`, `OPENROUTER_API_KEY`. |

## Commands

```sh
just agent            # chat in the terminal; also serves the panel API on 127.0.0.1:8765
just panel            # control panel on http://localhost:5173
just relay            # relay on :8080, reload
just reverse          # phone localhost:8080 → Mac (USB)
just bridge-install   # build, install, enable the service
just bridge-enable    # re-enable the service only
just send '<json>'    # POST a command
just screenshot       # → screenshot.jpg
just tunnel           # ngrok, for running without the cable
just test             # all tests
just logs             # bridge logcat
```

Debug builds' Dev relay defaults to `ws://localhost:8080/ws/device` and the `.env` token. Prod is the deployed relay, entered once.

## Agent

One owner, one conversation, any channel. Everything that wakes the orchestrator is an `Event`; everything it says goes out through the gateway to an `Address`.

| File | Role |
|---|---|
| `messages.py` | `Address(channel, id)`, `Message(address, parts)`, `Part = Text \| Image`. Medium- and model-independent. |
| `models.py` | The model-library seam: `openrouter(name)`, `content(image)`, `prompt(parts)`, `Instructions` (string, or a callable read each run). |
| `events.py` | `Event`: `prompt()`, `reply_to`, `from_owner`, `attachments()`, `fallback(error)`. `OwnerMessage`, `TaskUpdate`. A new trigger (schedule, incoming call) is a new subclass. |
| `channels/` | `Channel`: `name`, `owner` (the owner's address there), `start(deliver)`, `send(message)`. Channels only move messages. `cli.py`: terminal, `/image <path> [text]`; only when there is one. `panel.py`: control panel channel plus its local API (transcript, tasks, steps, images, prompts, and `/api/activity`, a server-sent event stream of live activity); routes async (SQLite). Its buttons send owner messages. `login.py`: sign in with GitHub, the owner only; a signed session cookie. `twilio.py`: the owner's texts via Twilio (`/twilio/sms`, checked against Twilio's signature, other numbers ignored); replies by Twilio's API, screenshots as a panel link (UK numbers can't send pictures). Also calls (`/twilio/voice`): Twilio's ConversationRelay does speech and turn-taking, Archangel trades text with it over a WebSocket (`/twilio/voice/relay`, signed the same way); on a call, everything is said on it; otherwise it rings only when asked for a call back (`report_via voice`), at most 3 times in 10 minutes, and texts after a hang-up (but not replies to what was said on that call, like "Goodbye.": `Message.answering`). Calls it makes use answering-machine detection: a voicemail gets the message after the beep and a hang-up (a recording is never taken for the owner); voicemails and missed calls get a text too. |
| `web.py` | The one web server; HTTP channels add their routes to `web.app`. Local (127.0.0.1, Host-checked) unless GitHub sign-in is set; then public on `$PORT`, trusting the proxy's headers, and serving the built panel too. `/health`. |
| `gateway.py` | Both ways: owner messages → `OwnerMessage` events, handled concurrently; outgoing messages → the channel in their address. Records the transcript. A failing channel is logged, not raised. |
| `orchestrator.py` | The agent the owner talks to; never touches the phone. `handle(event)`, one at a time. Tools: `start_task(goal, report_via?)`, `answer_task`, `redirect_task(id, channel)` ("text me when it's done"), `cancel_task`, `send(channel, text)`. Sees the channels and task list every turn. Replies go to `event.reply_to`; approvals only count when `event.from_owner`. |
| `tasks.py` | Runs pilots in the background, one on the phone at a time, the rest queued. Resumes waiting tasks with the owner's answer; cancels. Records each step; sends a `TaskUpdate` when a task finishes or needs the owner. |
| `store.py` | `data/archangel.db`: the one history (continuous), transcript, tasks (with `report_to`) and their steps, edited prompts. Images (screens, chat pictures) are files in `data/images/`, deleted after 3 days. Paused runs keep only their latest screen. |
| `prompts.py` | Every prompt by name. Defaults live in code beside their agents; panel edits live in the store and win from the agent's next run (`Prompts.live(name)`). |
| `history.py` | Context trimming: last 40 messages (cut at turn starts), last 2 screenshots. |
| `instructions.md` | Orchestrator prompt. |
| `logs.py` | `logs/<time>/agent.log` at DEBUG (`logs/latest` → newest, last 20 kept). `logs.run(agent, …)` logs each tool call, result and invalid call. |
| `activity.py` | Live activity for the cockpit, announced like logging: `hop(target, kind, text)`, `call(...)` for a request and its reply. Request, task and actor ids ride in context variables, so nothing passes them along. Roles are declared at the source (`declare(name, role)` for channels; agent runs and calls declare the rest), so the cockpit hard-codes no names. Never waits or raises; a slow listener misses notices; nothing is stored. |
| `phone/pilot.py` | Sub-agent for one task: act → screenshot → decide. `Pilot(model, Kit(phone, vision, guard, investigator))`. Tools: `open_app`, `close_app` (launch, recents, swipe its card up, home: no model guesswork), `tap(target)`, `type_text`, `scroll`, `press`, `look`, `ask_owner`. Ends with `done` (then home, unless `leave_open`) or `give_up`; 5 failures in a row or 40 steps end it as stuck. Pauses (resumably) as `needs_approval` or `needs_answer`. Going in circles (a step 3 times in 6, scrolls aside; or the same failure twice) gets a second opinion, at most 2 per run. |
| `phone/guard.py` | Checks every tap (and Enter) with a separate model call; anything that sends, pays, logs in, deletes or changes settings pauses for approval. Fails closed. |
| `phone/investigator.py` | The fallback model reads the last 3 screens and recent steps of a task going nowhere: what's in the way, how past it. Also explains stuck reports. |
| `phone/vision.py` | Finds things on the screen: `Vision.find(screen, target) → Box \| None`. `JevVision` (TypeSafe Jev via OpenRouter decisions API), `ModelVision` (any vision model), `FallbackVision` (the fallback vision model on a miss or failure). |
| `phone/relay.py` | `Phone`: relay client. Positions are screen fractions, turned into pixels with the latest screenshot's size (so rotation is fine); screenshots are half-scale JPEG at quality 30 (Jev rejects WebP; 30 locates as well as 80). Phone offline (503): waits up to 60 s, then resends. Lost mid-command (502): resends only read-only commands. |

The pilot never handles coordinates: it describes what to tap, vision finds the box, the centre gets tapped.

| Env | Default |
|---|---|
| `ARCHANGEL_MODEL` | `anthropic/claude-sonnet-5.5` (orchestrator, pilot, guard) |
| `ARCHANGEL_VISION_MODEL` | `typesafe/jev-1.14`; any other OpenRouter vision model uses `ModelVision` |
| `ARCHANGEL_FALLBACK_VISION_MODEL` | `openai/gpt-6-luna` (when vision misses, and the investigator). Must see images. |
| `ARCHANGEL_RELAY_URL` | `http://localhost:8080` |
| `ARCHANGEL_DEVICE` | unset: the one phone connected to the relay. Set it only to choose between several. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER`, `ARCHANGEL_PHONE` | unset: no sms or calls. Set: texts and calls with the owner's mobile (`ARCHANGEL_PHONE`, `+44…`) via Archangel's number. Deployed only: Twilio needs the public URL (`RAILWAY_PUBLIC_DOMAIN`, or `ARCHANGEL_PUBLIC_URL`). |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `ARCHANGEL_OWNER` | unset: the panel is local. Set (a GitHub OAuth App, and your GitHub username): public, sign-in required. |

Errors: a failed task (phone offline, stuck, model misbehaving) is a task update; the orchestrator retries once or tells the owner. Only orchestrator failures reach the owner raw, via `event.fallback(error)`. Tools get 3 retries for invalid calls. Model API errors (429, 5xx, network) are retried by Pydantic AI's OpenRouter client (it uses the `openai` package as its HTTP client; every call goes to OpenRouter).

Human-in-the-loop: the pilot pauses via Pydantic AI deferred tools (`ApprovalRequired` from the guard, `CallDeferred` from `ask_owner`). The paused run is saved with the task, so an answer works even after a restart. Tasks running at shutdown come back `interrupted`, and at startup each is reported where it was due (the orchestrator may retry it).

**Investigating a run:** the task's steps and screens in the control panel, then `tail -100 apps/agent/logs/latest/agent.log`. `jev` lines show Jev's raw answers (noul, box, confidence).

Tests use `FunctionModel` and `httpx.MockTransport`; no API calls. Shared fakes in `tests/fakes.py`; the `store` fixture is a fresh store in a temp dir.
Jev gotcha: through OpenRouter, images must be `{"type": "image_url", "image_url": {"url": "data:…"}}` in `state`; TypeSafe's `assets` format is silently ignored.
New channel = one `Channel` subclass registered in `__main__.py`; the orchestrator only sees its name ("via telegram") and can route to it. Images in replies (approval screenshots) must be shown by the channel; the CLI saves them to a temp file and prints the path.

## Bridge

Flat package `tech.arrakis.archangel.bridge`.

| File | Role |
|---|---|
| `BridgeService` | AccessibilityService. Owns the connection while enabled. No foreground service needed. |
| `Connection` | OkHttp WebSocket. 15 s pings, 1–30 s jittered backoff, early retry on network return. `Connection.status`. |
| `CommandExecutor` | Decode, run serially, never throw. Replays replies for repeated ids (last 64). |
| `Device` / `AndroidDevice` | Phone capabilities behind an interface; executor tests run on the JVM. |
| `Protocol` | Wire types. |
| `Config` / `ConfigStore` | Dev and Prod relays (URL and token each), which is on, and the device name. One SharedPreferences value; a tap switches. |
| `MainActivity` | One Compose screen: status, config, settings shortcuts. |

minSdk 30, targetSdk 36, compileSdk 37, JDK 17. Versions in `gradle/libs.versions.toml`. Release builds: `wss://` only, no baked-in URL or token.

## Relay

| File | Role |
|---|---|
| `hub.py` | Devices by name; one future per in-flight command. Disconnect fails pending. |
| `app.py` | `create_app()`: routes, Bearer auth, 503/504/502 mapping. |
| `config.py` | `ARCHANGEL_TOKEN` (required), `PORT`, constants. |
| `__main__.py` | Production entrypoint, `python -m relay`. |

## Conventions

- Small, flat, obvious. Deep modules, small interfaces (Ousterhout). No abstraction until needed.
- Agent follows 12-Factor Agents: own the prompt, context and control flow.
- Defaults over config. New env vars need a real reason.
- Comments say *why*, not *what*.
- Caller-facing errors use codes from `docs/protocol.md`. The bridge must never crash: Android disables crashing accessibility services.
- Update README.md, this file and `docs/` in the same commit as the change. Short, direct.

## Deploy

Railway project `archangel`, two services, both deployed from `main` on push. Settings live on the services (Railway deprecated `railway.json`); all have a healthcheck `/health` and restart on failure.

- **relay:** root dir `apps/relay`, start `python -m relay`, watch `apps/relay/**`. `PORT=8080`, `ARCHANGEL_TOKEN`: a long random one, not the dev token. Phone URL: `wss://relay-production-0aad.up.railway.app/ws/device`.
- **agent:** root dir `/`, `RAILWAY_DOCKERFILE_PATH=apps/agent/Dockerfile` (builds the panel, serves it with the API), watch `apps/agent/**`, `apps/panel/**`. Volume at `/app/apps/agent/data`. At `https://agent-production-e06c.up.railway.app`. Variables: `PORT=8080`, `ARCHANGEL_TOKEN` (`${{relay.ARCHANGEL_TOKEN}}`), `OPENROUTER_API_KEY`, `ARCHANGEL_RELAY_URL=https://<relay-domain>`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `ARCHANGEL_OWNER`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER`, `ARCHANGEL_PHONE`.
- **Twilio:** the number's "A message comes in" webhook is `https://<agent-domain>/twilio/sms` and "A call comes in" is `…/twilio/voice` (both POST). ConversationRelay needs the AI/ML addendum accepted once (Console → Voice → Settings → Privacy & Security). The Twilio CLI is logged in on the owner's Mac (API key, so it can't read the auth token).
- **GitHub OAuth App** (github.com/settings/developers): homepage `https://<agent-domain>`, callback `https://<agent-domain>/auth/callback`.
- No terminal there: the panel, sms and voice are the channels. `logs/` doesn't survive a deploy; Railway's log view has the console lines.
- **Bridge:** `just bridge-install`. Later: signed APK from CI via GitHub Releases.

## Limits

- `FLAG_SECURE` screens (banking, payments) screenshot black; `ui_tree` usually still works.
- Some apps detect accessibility services or ignore synthetic input.
- Can't pass a secure lock screen. Unattended: no lock, "Stay awake while charging", battery optimisation off.
- Browser-sideloaded on Android 13+: App info → ⋮ → "Allow restricted settings" before enabling. `adb install` unaffected.
- Install or force-stop switches the service off.
- `type` uses `ACTION_SET_TEXT`; editors that reject it (X's compose box) get a clipboard paste instead, which overwrites the clipboard.
- The guard is a model judging a screenshot: strong, not perfect. It adds one model call per tap.
- The control panel's buttons are messages to the orchestrator, so approving from it costs one model call.
- A waiting task doesn't hold the phone; if another task runs meanwhile, the approved tap re-locates its target on whatever screen is there.
- Android rate-limits screenshots; the bridge retries briefly.
