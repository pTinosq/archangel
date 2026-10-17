# Archangel

AI agent that operates a real Android phone. No cable, ADB or root at runtime. v1: the device interface; agent not built yet.

```
caller ──HTTP──▶ relay ◀──WebSocket── bridge (phone)
```

## Layout

| Path | What |
|---|---|
| `apps/bridge/` | Android app, Kotlin. AccessibilityService that dials the relay and runs commands. |
| `apps/relay/` | Python relay, FastAPI + uv. Holds phone connections, exposes the HTTP API. |
| `docs/protocol.md` | Wire contract. Change it first, then both apps. |
| `justfile` | All dev tasks. `just` lists them. |
| `.env` | `ARCHANGEL_TOKEN` only. |

Future `apps/agent/` calls the relay over HTTP.

## Commands

```sh
just relay            # relay on :8080, reload
just reverse          # phone localhost:8080 → Mac (USB)
just bridge-install   # build, install, enable the service
just bridge-enable    # re-enable the service only
just send '<json>'    # POST a command
just screenshot       # → screenshot.jpg
just tunnel           # ngrok, for running without the cable
just test             # pytest + Kotlin unit tests
just logs             # bridge logcat
```

Debug builds default to `ws://localhost:8080/ws/device` and the `.env` token.

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

- Small, flat, obvious. No abstraction until needed.
- Defaults over config. New env vars need a real reason.
- Comments say *why*, not *what*.
- Caller-facing errors use codes from `docs/protocol.md`. The bridge must never crash: Android disables crashing accessibility services.
- Update README.md, this file and `docs/protocol.md` in the same commit as the change. Short, direct.

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
