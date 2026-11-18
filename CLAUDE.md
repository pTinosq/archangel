# Archangel

AI agent that operates a real Android phone and a Windows PC. No cable, ADB or root at runtime. Progress: `docs/roadmap.md`.

```
channels ⇄ gateway ⇄ orchestrator ──▶ tasks ──▶ pilots ──▶ relay ◀──WebSocket── bridges (phone, PC)
 (any medium,  (events in,  (one conversation,  (background     (action)
  one owner)    routing out)  kept as threads;    sub-agents)
                              talks, manages)
```

**This repo is public. Nothing about the owner's real life goes in it, anywhere:** code, tests, evals, fixtures, docs, roadmap, commit messages, PRs. No real names (friends, family, the owner's), places, devices, accounts, groups, purchases, numbers or anything else from conversations, the twin or prod. Make it up (Sam, Priya, Tom; Ofcom's `+447700900xxx` numbers), even when quoting something the owner said. Check the diff for it before every commit.

## Layout

| Path | What |
|---|---|
| `apps/agent/` | Python, Pydantic AI + uv. Channels, gateway, orchestrator, tasks, device pilots (phone, PC). |
| `apps/bridge/` | Android bridge, Kotlin. AccessibilityService that dials the relay and runs commands. |
| `apps/bridge-windows/` | Windows bridge, Python, built into an exe by CI. A service that dials the relay and runs commands, the lock screen included ([README](apps/bridge-windows/README.md)). |
| `apps/relay/` | Python relay, FastAPI + uv. Holds device connections, exposes the HTTP API. |
| `apps/panel/` | Control panel, React + Vite + plain CSS; polls the agent's API every second. Pages at real paths (`route.tsx`), each also listed in the agent's `web.PAGES`. Look: rules at the top of `styles.css` (a state changes colour, never size). `blueprint.ts` (the Architecture tab) is written by hand from the code: keep it true when the flow changes. React Flow gotchas, sounds, pages: `docs/panel.md`. |
| `docs/protocol.md` | Device wire contract, one for every bridge (platform-specific commands marked). Change it first, then bridges and relay. |
| `docs/roadmap.md` | Milestones. Tick them off as they land. |
| `docs/agent.md`, `panel.md`, `threads.md`, `twin.md`, `styles.md` | The detail behind the rows below. |
| `justfile` | All dev tasks. `just` lists them. |
| `.env` | `ARCHANGEL_TOKEN`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY` (optional). |

## Commands

```sh
just agent            # chat in the terminal; also serves the panel API on 127.0.0.1:8765
just panel            # control panel on http://localhost:5173
just relay            # relay on :8080, reload
just reverse          # phone localhost:8080 → Mac (USB)
just bridge-install   # build, install, enable the service
just bridge-enable    # re-enable the service only
just send '<json>'    # POST a command to the phone
just screenshot       # → screenshot.jpg
just pc-send '<json>' # the same, to the PC
just pc-screenshot
just bridge-windows-test  # the Windows bridge's tests that run anywhere (CI runs the rest on Windows)
just tunnel           # ngrok, for running without the cable
just test             # all tests
just style owner sms  # which writing style an audience gets, and its text (no arguments: list them)
just twin alex        # the context twin on a real model, in a terminal, from a made-up graph
just twin-eval        # the twin's evals on a real model (pass rates; costs a little)
just threads-eval     # the thread router's evals on a real model (pass rates; costs a little)
just logs             # bridge logcat
```

Debug builds' Dev relay defaults to `ws://localhost:8080/ws/device` and the `.env` token. Prod is the deployed relay, entered once.

## Agent

One owner, one conversation, any channel, kept as threads (one per topic, `docs/threads.md`). Everything that wakes the orchestrator is an `Event`; everything it says goes out through the gateway to an `Address`. Module detail: `docs/agent.md`.

| File | Role |
|---|---|
| `messages.py` | `Address(channel, id)`, `Message(address, parts)`, `Part = Text \| Image`. Medium- and model-independent. |
| `models.py` | The model-library seam: `openrouter(name, timeout=120)`, `content(image)`, `prompt(parts)`, `Instructions` (a string, or a callable read each run). Only `TRUSTED` hosts, in the owner's order (a new model may need its host added). The orchestrator runs with `think=False`; background agents reason. |
| `events.py` | `Event`: `prompt()`, `reply_to`, `from_owner`, `attachments()`, `fallback(error)`, `thread` (if known). `OwnerMessage`, `TaskUpdate`, `Notice` (something a channel saw: a call went to voicemail), `Scheduled` (something due now: the owner's words, and how late if Archangel was down). A new trigger (an incoming call) is a new subclass. |
| `channels/` | `Channel`: `name`, `shows` (what it can show: a call can't show a picture), `owner`, `start(deliver)`, `send(message)`. Channels only move messages. `cli.py` terminal; `panel.py` the panel's channel and API (secrets never leave it); `login.py` GitHub sign-in, the owner only; `twilio.py` texts and calls, Twilio-signature-checked, other numbers ignored. |
| `web.py` | The one web server; HTTP channels add routes to `web.app`. Local (127.0.0.1, Host-checked) unless GitHub sign-in is set, then public on `$PORT`, serving the built panel. `/health`. |
| `links.py` | Signed links that open without sign-in and expire after a day (screenshots for texts, the location page). Only with a public address. |
| `location.py` | `owner_location`: the owner's last shared place (the phone stays at home), and a `/where/…` link whose page shares the browser's location once. |
| `gateway.py` | Owner messages → `OwnerMessage` events, handled concurrently; outgoing messages → the channel in their address. Records the transcript: an owner's line is filed under its thread once routed; a reply carries its thread (`Message.thread`). A failing channel is logged, not raised. |
| `orchestrator.py` | The agent the owner talks to; never acts on a device itself. Handles one event at a time, in its thread, seeing that thread's history only. Tools and rules: below. |
| `tasks.py` | Runs pilots in the background, one task per device (phone, PC) at a time, the rest queued; research, connector and code tasks run beside them. Briefs each device or connectors task from the twin first (not research). Resumes waiting tasks with the owner's answer; passes what the owner says to a running one (read on its next step); cancels. Records each step; a `TaskUpdate` when a task finishes or needs the owner, to the thread that started it (`Task.thread`). |
| `store.py` | `data/archangel.db`: transcript, tasks (`report_to`, `kind`, `public`, `thread`, a code task's `github_run`) and their steps, research pages (`data/pages/`), edited prompts, connectors and quick tools (as JSON), the owner's last location, each device's latest hello (a sleeping PC is woken by the card it named). Threads, the schedule and the twin own their tables (`threads/store.py`, `schedule.py`, `twin/graph.py`). Images are files in `data/images/`, deleted after 3 days; paused runs keep only their latest screen. |
| `prompts.py` | Every prompt by name ("phone pilot", "pc pilot", "thread router"…). Defaults in code beside their agents; panel edits in the store win from the next run (`Prompts.live(name)`). |
| `schedule.py` | Later (M13): one-offs and cron repeats as rows and a sleeping loop (no Temporal). Marked fired, or moved on, before firing, so a crash never fires twice; repeats end after `max_runs` (default 50, at most 200); what came due while down fires once on start. |
| `coder.py` | Self-improvement (M14): runs `.github/workflows/improve.yml` (Claude Code in Actions; its token a repo secret, never here), follows the run, reads its `result` artifact (PR and summary, an answer, or Claude's error); a code task keeps its run's id, so it outlives a deploy; `merge(pr)` squashes. The request, run and PR are public. |
| `history.py` | Context trimming: last 2 screenshots; 100 messages (cut at turn starts) only as a backstop: a full thread is carried on in a new one first. |
| `threads/` | Threads (M18, `docs/threads.md`). `Threads.route(event)`: the owner's message where the router (quick model, no reasoning) chooses, or to the thread chosen in the panel; a task's update to its thread; a notice to the latest. Never fails. Closes on a wrap-up or 3 quiet hours, summarised as it closes; at 30k tokens carried on in a new thread. |
| `instructions.md` | Orchestrator prompt. |
| `logs.py` | `logs/<time>/agent.log` at DEBUG (`logs/latest` → newest, last 20 kept). `logs.run(agent, …)` logs each tool call, result and invalid call. |
| `activity.py` | Live activity for the cockpit, announced like logging: `hop(target, kind, text)`, `call(...)`. Ids ride in context variables; roles are declared at the source (`declare(name, role)`), so the cockpit hard-codes no names. Never waits or raises; a slow listener misses notices; nothing is stored. |
| `research.py` | The web, never the phone: `search`, `read` (Tavily, seconds, answered inline), `research` (Perplexity Pro Search, under a minute, ~3p; `deep`: ~4 minutes, ~60p). |
| `reports.py` | Topic and material in, `Report(tldr, title, pages)` out; fixed phone-shaped layout, saved as finished HTML. Served at `/reports/{id}`, behind sign-in unless `Task.public`, under a CSP that lets nothing in it run or load. |
| `connectors.py` | The owner's services as MCP servers (`MCPToolset`), signed in to from the panel: hosted ones by OAuth, Gmail and Calendar by `workspace-mcp` per Google account, only for a run. Tools prefixed with the connector's name; read-only ones (`readOnlyHint`) run at once, the rest raise `ApprovalRequired`. One per account, grouped by profile ("work", "personal"). `gmail.py` tidies messages. |
| `tools/` | Quick tools (M15): live data answered inline, no device (TfL, weather, currency, dates, places; all keyless). Add one: subclass `Tool`, list it in `BUILT_IN`. Function names and docstrings are what the orchestrator sees. A failing source is answered, never raised. |
| `styles/` | How it writes (M20, `docs/styles.md`): a markdown profile per audience over `base.md`; `writing_style(to, via)` for the agents. The owner's own (naming real people) go in `data/styles/`, never the repo. |
| `twin/` | The context twin (M12, `docs/twin.md`). `graph.py`: nodes with markdown pages, linked with a relation and a preview; keeps the invariants (unique names and aliases, every edit recorded with its source, stale previews found). `reader.py` (quick model, read-only), `writer.py` (an episode in, edits out). `Twin`: `root()`, `hear(said)`, `brief(goal)`, `tools`; reads each conversation after 10 quiet minutes, `remember` at once. |
| `evals_tab.py` | The panel's Evals tab: lists, shows and starts the twin's eval runs. Local agent only, never public. |
| `helper.py` | Sub-agent for connector tasks: goal and tools in, a short answer out. Pauses for approvals and `ask_owner`, resumably, like the pilot. |
| `runs.py` | What the pilot and helper share: `Outcome`, the owner's `Answer`, `Record`, turning a paused run's requests and the owner's answer into results. |
| `devices/pilot.py` | The device sub-agent, shared by every device: act → screenshot → decide. A `Platform` (`phone.py`, `pc.py`) brings tools, prompt and tidying up. Ends with `done` (a report to Archangel, not the owner) or `give_up`; judges its own progress, no stuck rules (120 steps is a runaway backstop). Pauses resumably as `needs_approval` or `needs_answer`. What a run changed (faked location, unlocked PC) is put back when it ends or pauses. |
| `devices/phone.py` | `close_app`, `tap`, `type_text`, `scroll`, `press` (and `unlock`, no PIN), `set_location`. Prompt `phone.md`. |
| `devices/pc.py` | `click`, `type_text` (line breaks never submit), `scroll`, `press` (consequential keys guarded), `unlock` (PIN kept by the bridge; relocked after). Prompt `pc.md`. |
| `devices/guard.py` | Checks every tap or click (and consequential keys) with a separate model call; anything that sends, pays, logs in, deletes or changes settings pauses for approval (a search's filters aren't settings). Sees what the owner has said to the task (kept in memory by `tasks.py`), so nothing agreed is asked again. Fails closed. |
| `devices/investigator.py` | The fallback model reads the last 3 screens and recent steps for a second opinion: what's in the way, how past it. Also explains stuck reports. |
| `devices/vision.py` | `Vision.find(screen, target) → Box \| None`: `JevVision` (TypeSafe Jev via OpenRouter), `ModelVision` (any vision model), `FallbackVision` (the fallback model on a miss or failure). |
| `devices/relay.py` | `Device(client, platform)`: a device through the relay. Positions are screen fractions. Screenshots sized for Jev (no WebP; a side over about 1568 rejected). Offline: waits, and wakes a known PC by Wake-on-LAN; lost mid-command: resends only read-only commands. |

The pilot never handles coordinates: it describes what to tap, vision finds the box, the centre gets tapped.

**Orchestrator rules** (tools in `docs/agent.md#orchestrator`):

- Only from the owner's own message: `research(deep=True)` (once they've agreed to its cost), making a report public, `improve` and `merge`. Approvals only when `event.from_owner`: it never decides one for the owner.
- The owner always gets an answer. On anything else it may `stay_quiet`.
- A repeat's runs are silent in code, whatever the model writes, except the one that ends it and the last.
- It may cancel on its own only what was scheduled in its thread.
- Try a quick tool, then the web, then research, a device last.
- Every turn it sees the twin's root page and what the reader finds bears on what the owner said, the time (`Europe/London`), its thread and the others, quick tools, channels, devices, tasks and what's scheduled.

| Env | Default |
|---|---|
| `ARCHANGEL_MODEL` | `anthropic/claude-sonnet-5.5` (orchestrator, pilot, guard) |
| `ARCHANGEL_VISION_MODEL` | `typesafe/jev-1.14`; any other OpenRouter vision model uses `ModelVision` |
| `ARCHANGEL_FALLBACK_VISION_MODEL` | `openai/gpt-6-luna` (when vision misses, and the investigator). Must see images. |
| `ARCHANGEL_RELAY_URL` | `http://localhost:8080` |
| `ARCHANGEL_DEVICE` | unset: the one phone and the one PC connected to the relay. Set it (names, comma-separated) only to choose between several. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER`, `ARCHANGEL_PHONE` | unset: no sms or calls. Set: texts and calls with the owner's mobile (`ARCHANGEL_PHONE`, `+44…`) via Archangel's number. Deployed only: Twilio needs the public URL (`RAILWAY_PUBLIC_DOMAIN`, or `ARCHANGEL_PUBLIC_URL`). |
| Quick tools | Not env: keyless, on by default; switched and set on the Connectors page (TfL's optional app key, weather's default place). |
| Connectors | Not env: entered on the panel's Connectors page and kept in the store. Google needs a Google Cloud OAuth client (web application; Gmail and Calendar APIs on; redirect URI `<site>/connectors/callback`; published, or testing sign-ins expire in 7 days). Slack needs an internal Slack app with the same redirect URL. |
| `TAVILY_API_KEY` | unset: no quick web search (deep research still works). Free tier: 1,000 searches a month. |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `ARCHANGEL_OWNER` | unset: the panel is local. Set (a GitHub OAuth App, and your GitHub username): public, sign-in required. |
| `GITHUB_TOKEN` | unset: Archangel can't improve itself. Set: a fine-grained token for this repo only (Actions, Contents, Pull requests: read and write). The workflow also needs the repo secret `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`). |

Errors: a failed task (phone offline, stuck, model misbehaving) is a task update; the orchestrator retries once or tells the owner. Only orchestrator failures reach the owner raw, via `event.fallback(error)`. Tools get 3 retries for invalid calls. Model API errors (429, 5xx, network) are retried by Pydantic AI's OpenRouter client (it uses the `openai` package as its HTTP client; every call goes to OpenRouter).

Human-in-the-loop: the pilot pauses via Pydantic AI deferred tools (`ApprovalRequired` from the guard, `CallDeferred` from `ask_owner`). The paused run is saved with the task, so an answer works even after a restart. Tasks running at shutdown come back `interrupted`, and at startup each is reported where it was due (the orchestrator may retry it); a code task whose GitHub run is known follows it instead.

**Investigating a run:** the task's steps and screens in the control panel, then `tail -100 apps/agent/logs/latest/agent.log`. `jev` lines show Jev's raw answers (noul, box, confidence).

Tests use `FunctionModel` and `httpx.MockTransport`; no API calls. Shared fakes in `tests/fakes.py` (`fakes.threads(store)`: real threads with a router that carries on the latest, as one conversation did); the `store` fixture is a fresh store in a temp dir. The twin and the thread router also have evals on a real model, against a made-up owner, never the real one (`apps/agent/evals/twin/`, `apps/agent/evals/threads/`; `docs/twin.md#evals`, `docs/threads.md#evals`).

Jev gotcha: through OpenRouter, images must be `{"type": "image_url", "image_url": {"url": "data:…"}}` in `state`; TypeSafe's `assets` format is silently ignored.

New channel = one `Channel` subclass registered in `__main__.py`; the orchestrator only sees its name ("via telegram") and can route to it. Images in replies (approval screenshots) must be shown by the channel; the CLI saves them to a temp file and prints the path. Planned, not built (M21, `docs/roadmap.md`): an email channel with an inbox of its own; mail from the owner is an `OwnerMessage`, other mail a `Notice`.

## Android bridge

Flat package `tech.arrakis.archangel.bridge`.

| File | Role |
|---|---|
| `BridgeService` | AccessibilityService. Owns the connection while enabled. No foreground service needed. |
| `Connection` | OkHttp WebSocket. 15 s pings, 1–30 s jittered backoff, early retry on network return. `Connection.status`. |
| `CommandExecutor` | Decode, run serially, never throw. Replays replies for repeated ids (last 64). |
| `Device` / `AndroidDevice` | Phone capabilities behind an interface; executor tests run on the JVM. |
| `WakeOnLan` | `wake`: a magic packet broadcast on the phone's network, waking the PC. |
| `MockLocation` | `location`: test providers for GPS, network and fused, resent every second; removed on `location` without coordinates or when the service stops. Needs the bridge chosen as the mock location app (a button on its screen opens Developer options). |
| `Protocol` | Wire types. |
| `Config` / `ConfigStore` | Dev and Prod relays (URL and token each), which is on, and the device name. One SharedPreferences value; a tap switches. |
| `MainActivity` | One Compose screen: status, config, settings shortcuts. |

The service declares itself an accessibility tool (`isAccessibilityTool`): otherwise Android hides "data sensitive" windows like permission prompts from it and silently drops its taps there.

minSdk 30, targetSdk 36, compileSdk 37, JDK 17. Versions in `gradle/libs.versions.toml`. Release builds: `wss://` only, no baked-in URL or token.

## Windows bridge

`apps/bridge-windows/`, package `bridge`, the Android bridge's shape in Python.

| File | Role |
|---|---|
| `protocol.py`, `executor.py` | Wire types; serial, never raises, replays the last 64 ids. |
| `device.py` / `windows.py` | The PC behind an interface; tests use a fake. |
| `connection.py` | 15 s pings, 1–30 s jittered backoff. |
| `config.py` | Dev and Prod relays, name, PIN. One JSON file in `C:\ProgramData\Archangel`, readable only by administrators and SYSTEM; the PIN DPAPI-encrypted for the machine. |
| `win32.py` | Windows-only: GDI capture, `SendInput`, the input desktop, processes as the signed-in user. |
| `uia.py` | Windows-only: UI Automation tree. |
| `service.py`, `settings.py` | The service; the tkinter window (Save installs and starts the service). |

The lock screen and administrator prompts are the `Winlogon` desktop, which only SYSTEM can open, and a service (session 0) has no screen. So the service starts the bridge as SYSTEM in the console session and keeps it there (restarted when the session changes or it exits); before each command its input thread moves onto whichever desktop has input. Apps it opens and the Start menu it lists are the signed-in user's (`WTSQueryUserToken`). `unlock` types the PIN as keys, Enter only if still locked (never into an unlocked desktop).

PyInstaller folder build (`--onedir`): a one-file exe runs the app as a child process, which a service can't be. CI (`.github/workflows/bridge-windows.yml`, on a Windows runner) runs every test on its real desktop, builds, runs the exe against the relay in the foreground and as the service, and publishes `archangel-bridge.zip` to the `bridge-windows` release from `main`.

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
- Update README.md, this file and `docs/` in the same commit as the change. Short, direct. A row here says what a file is and what's not obvious; the rest goes in `docs/`.
- **The repo is public:** nothing about the owner's real life (top of this file). Check the diff before every commit.

## Deploy

Railway project `archangel`, two services, both deployed from `main` on push. Settings live on the services (Railway deprecated `railway.json`); all have a healthcheck `/health` and restart on failure.

- **relay:** root dir `apps/relay`, start `python -m relay`, watch `apps/relay/**`. `PORT=8080`, `ARCHANGEL_TOKEN`: a long random one, not the dev token. Phone URL: `wss://relay-production-0aad.up.railway.app/ws/device`.
- **agent:** root dir `/`, `RAILWAY_DOCKERFILE_PATH=apps/agent/Dockerfile` (builds the panel, serves it with the API; pre-fetches workspace-mcp), watch `apps/agent/**`, `apps/panel/**`. Volume at `/app/apps/agent/data`. At `https://archangel.tinos.xyz` (Cloudflare proxy, the Railway custom domain; `ARCHANGEL_PUBLIC_URL` set to it); the old `agent-production-e06c.up.railway.app` redirects there. Variables: `PORT=8080`, `ARCHANGEL_TOKEN` (`${{relay.ARCHANGEL_TOKEN}}`), `OPENROUTER_API_KEY`, `ARCHANGEL_RELAY_URL=https://<relay-domain>`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `ARCHANGEL_OWNER`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER`, `ARCHANGEL_PHONE`, `TAVILY_API_KEY`, `GITHUB_TOKEN`.
- **Twilio:** the number's "A message comes in" webhook is `https://<agent-domain>/twilio/sms` and "A call comes in" is `…/twilio/voice` (both POST). ConversationRelay needs the AI/ML addendum accepted once (Console → Voice → Settings → Privacy & Security). The Twilio CLI is logged in on the owner's Mac (API key, so it can't read the auth token).
- **Self-improvement (M14):** repo secret `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`, on the owner's subscription), "Allow GitHub Actions to create and approve pull requests" on (Settings → Actions → General), and `GITHUB_TOKEN` on the agent.
- **GitHub OAuth App** (github.com/settings/developers): homepage `https://<agent-domain>`, callback `https://<agent-domain>/auth/callback`.
- No terminal there: the panel, sms and voice are the channels. `logs/` doesn't survive a deploy; Railway's log view has the console lines.
- **Bridge:** `just bridge-install`. Later: signed APK from CI via GitHub Releases.
- **Windows bridge:** the zip from the `bridge-windows` release; its window takes the prod relay URL and token.

## Limits

- `FLAG_SECURE` screens (banking, payments) screenshot black; `ui_tree` usually still works.
- Some apps detect accessibility services or ignore synthetic input.
- The phone has no SIM: no texts or calls on it. The orchestrator prompt says so (edit it there if that changes).
- Can't pass a secure lock screen. Unattended: no lock, "Stay awake while charging", battery optimisation off.
- Browser-sideloaded on Android 13+: App info → ⋮ → "Allow restricted settings" before enabling. `adb install` unaffected.
- Install or force-stop switches the service off.
- `type` uses `ACTION_SET_TEXT`; editors that reject it (X's compose box) get a clipboard paste instead, which overwrites the clipboard.
- The guard is a model judging a screenshot: strong, not perfect. It adds one model call per tap.
- The control panel's buttons are messages to the orchestrator, so approving from it costs one model call.
- A waiting task doesn't hold the phone; if another task runs meanwhile, the approved tap re-locates its target on whatever screen is there.
- Android rate-limits screenshots; the bridge retries briefly.
- The PC: its primary display only. Someone using it while a task runs shares the mouse and keyboard with the pilot. Typing goes as characters, so an app that only reads keys (some games, remote desktops) misses it. A window running as administrator can't be clicked without the service. A UAC prompt for a standard account (a password) isn't handled. Ctrl+Alt+Del can't be sent, so a PC that demands it to sign in can't be unlocked. Waking needs Wake-on-LAN set up and, usually, a wired connection; the phone must be on the same network.
- A faked location can be seen through (apps reading Wi-Fi or cell location, or checking for mock locations), and Uber or banking apps may flag it: the pilot types an address wherever an app takes one.
