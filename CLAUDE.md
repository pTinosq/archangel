# Archangel

An AI agent that operates a real Android phone with no cable, no ADB and no root. The agent itself is not built yet;
v1 is the device interface it will use.

```
caller (Postman, later the agent) ──HTTP──▶ relay ◀──WebSocket── bridge (on the phone)
```

## Layout

```
apps/bridge/   Android app (Kotlin). An AccessibilityService that dials the relay and executes commands.
apps/relay/    Python relay (FastAPI, uv). Holds phone connections; exposes an HTTP command API.
docs/protocol.md   The wire contract. Both apps implement it; change it first, then them.
justfile       Every dev task. Run `just` to list them.
.env           ARCHANGEL_TOKEN only. Copy from .env.example.
```

A future `apps/agent/` sits alongside these and calls the relay over HTTP.

## Commands

```sh
just relay            # relay on :8080 with reload
just bridge-install   # build + install debug APK over USB
just bridge-enable    # switch the accessibility service on (each install switches it off)
just reverse          # phone's localhost:8080 → this machine, over USB
just tunnel           # ngrok, for running without the cable
just send '{"type":"key","key":"home"}'
just screenshot       # → screenshot.jpg
just test             # pytest + Kotlin unit tests
just logs             # adb logcat for the bridge
```

First run: `cp .env.example .env`, set a token, then `just relay`, `just reverse`, `just bridge-install`, `just bridge-enable`.
Debug builds default to `ws://localhost:8080/ws/device` and the token from `.env`, so they connect with no typing.

## Bridge (`apps/bridge`)

Flat package `tech.arrakis.archangel.bridge`:

- `BridgeService` — the AccessibilityService. Owns the connection for as long as the service is enabled. No foreground service: the system keeps accessibility services alive, and they may start activities from the background.
- `Connection` — OkHttp WebSocket, 15 s pings, exponential backoff (1→30 s, jittered), reconnects early when the network returns. Publishes `Connection.status`.
- `CommandExecutor` — decodes, runs one command at a time, never throws, replays replies for repeated ids (last 64).
- `Device` / `AndroidDevice` — the phone's capabilities behind an interface, so the executor is unit-tested on the JVM.
- `Protocol` — serializable wire types. `Config` / `ConfigStore` — URL, token, device name in SharedPreferences.
- `MainActivity` — one Compose screen: status, config, shortcuts to the system settings it needs.

minSdk 30 (`takeScreenshot`), targetSdk 36, compileSdk 37, JDK 17. Versions live in `gradle/libs.versions.toml`.
Debug builds allow `ws://`; release builds require `wss://` and have no baked-in URL or token.

## Relay (`apps/relay`)

- `hub.py` — connected devices by name, and a future per in-flight command. Disconnect fails pending commands.
- `app.py` — `create_app()` factory: routes, Bearer auth (`hmac.compare_digest`), error mapping (503/504/502).
- `config.py` — env (`ARCHANGEL_TOKEN`, Railway's `PORT`) and constants. Missing token fails at startup.
- `python -m relay` is the production entrypoint (`railway.json`).

## Conventions

- Small, flat, obvious. No layer, file or abstraction until something needs it.
- Defaults over configuration. A new env var needs a real reason; constants live in code.
- Comments explain *why*, never *what*.
- Errors that reach callers have a code from `docs/protocol.md`; the bridge never crashes on a bad command (Android disables crashing accessibility services).
- Keep this file and `docs/protocol.md` current with every change.

## Deployment

- **Relay → Railway.** New service, root directory `apps/relay`, variable `ARCHANGEL_TOKEN`. Railway's TLS makes the phone URL `wss://<domain>/ws/device`.
- **Bridge → phone.** Now: `just bridge-install`. Later: signed release APK from CI, attached to GitHub Releases.

## Known limits

- `FLAG_SECURE` screens (banking, some payment and login screens) screenshot black; `ui_tree` often still works.
- Some apps detect accessibility services or ignore synthetic input.
- Gestures can't pass a secure lock screen. Unattended: no secure lock, "Stay awake while charging", battery optimisation off.
- Sideloaded from a browser on Android 13+, the accessibility toggle is greyed out until App info → ⋮ → "Allow restricted settings". `adb install` is unaffected.
- Force-stopping the app disables the service until it is re-enabled.
- Screenshots are rate-limited by Android; the bridge retries briefly before failing.
