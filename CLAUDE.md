# Archangel

AI agent that operates a real Android phone. No cable, ADB or root at runtime. Progress: `docs/roadmap.md`.

```
channels ──▶ gateway ──▶ brain ──tools──▶ relay ◀──WebSocket── bridge (phone)
 (comms)                (agentic)        (action)
```

## Layout

| Path | What |
|---|---|
| `apps/agent/` | Python, Pydantic AI + uv. Channels, gateway, brain. |
| `apps/bridge/` | Android app, Kotlin. AccessibilityService that dials the relay and runs commands. |
| `apps/relay/` | Python relay, FastAPI + uv. Holds phone connections, exposes the HTTP API. |
| `docs/protocol.md` | Phone wire contract. Change it first, then bridge and relay. |
| `docs/roadmap.md` | Milestones. Tick them off as they land. |
| `justfile` | All dev tasks. `just` lists them. |
| `.env` | `ARCHANGEL_TOKEN`, `OPENROUTER_API_KEY`. |

## Commands

```sh
just agent            # chat in the terminal
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

Debug builds default to `ws://localhost:8080/ws/device` and the `.env` token.

## Agent

| File | Role |
|---|---|
| `messages.py` | `Message(conversation, parts)`, `Part = Text \| Image`. Medium- and model-independent. |
| `channels/` | `Channel`: `start(deliver)`, `send(message)`. One class per medium. `cli.py`: terminal, `/image <path> [text]`. |
| `gateway.py` | Routes channels ⇄ brain. Never blocks a channel; one run at a time per conversation. |
| `brain.py` | Pydantic AI agent. `handle(message, reply)`; may reply any number of times. Tool: `use_phone(task)`. |
| `threads.py` | History per conversation; resets after 2 h idle. In memory until M3. |
| `instructions.md` | System prompt. |
| `phone/pilot.py` | Sub-agent for one phone task: act → screenshot → decide, max 40 steps. Tools: `open_app`, `tap(target)`, `type_text`, `scroll`, `press`, `look`. Keeps only the last 2 screenshots in context. |
| `phone/vision.py` | `Locator.locate(screenshot, target) → Box \| None`. `JevLocator` (TypeSafe Jev via OpenRouter decisions API) or `LlmLocator` (any vision model). |
| `phone/relay.py` | `Phone`: relay client. Positions are screen fractions; screenshots at half scale. |

The pilot never handles coordinates: it describes what to tap, the locator finds the box, the centre gets tapped.

| Env | Default |
|---|---|
| `ARCHANGEL_MODEL` | `anthropic/claude-sonnet-5.5` (brain + pilot) |
| `ARCHANGEL_VISION_MODEL` | `typesafe/jev-1.14`; any other OpenRouter vision model uses `LlmLocator` |
| `ARCHANGEL_RELAY_URL` | `http://localhost:8080` |
| `ARCHANGEL_DEVICE` | `pixel-6` |

Tests use `FunctionModel` and `httpx.MockTransport`; no API calls.
Jev gotcha: through OpenRouter, images must be `{"type": "image_url", "image_url": {"url": "data:…"}}` in `state`; TypeSafe's `assets` format is silently ignored.
New channel = one `Channel` subclass registered in `__main__.py`; the brain never knows which channel it serves.

## Bridge

Flat package `tech.arrakis.archangel.bridge`.

| File | Role |
|---|---|
| `BridgeService` | AccessibilityService. Owns the connection while enabled. No foreground service needed. |
| `Connection` | OkHttp WebSocket. 15 s pings, 1–30 s jittered backoff, early retry on network return. `Connection.status`. |
| `CommandExecutor` | Decode, run serially, never throw. Replays replies for repeated ids (last 64). |
| `Device` / `AndroidDevice` | Phone capabilities behind an interface; executor tests run on the JVM. |
| `Protocol` | Wire types. |
| `Config` / `ConfigStore` | URL, token, device name. One SharedPreferences value. |
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

- **Relay → Railway:** root dir `apps/relay`, set `ARCHANGEL_TOKEN`. Phone URL becomes `wss://<domain>/ws/device`.
- **Bridge:** `just bridge-install`. Later: signed APK from CI via GitHub Releases.

## Limits

- `FLAG_SECURE` screens (banking, payments) screenshot black; `ui_tree` usually still works.
- Some apps detect accessibility services or ignore synthetic input.
- Can't pass a secure lock screen. Unattended: no lock, "Stay awake while charging", battery optimisation off.
- Browser-sideloaded on Android 13+: App info → ⋮ → "Allow restricted settings" before enabling. `adb install` unaffected.
- Install or force-stop switches the service off.
- Android rate-limits screenshots; the bridge retries briefly.
