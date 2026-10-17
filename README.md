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
```

Other model: `ARCHANGEL_MODEL=google/gemini-3.5-flash just agent`.

## Drive the phone

```sh
just relay               # keep running
just reverse
just bridge-install
```

Open **Archangel Bridge** on the phone. It should say **Connected**. Then, with `Authorization: Bearer $ARCHANGEL_TOKEN`:

```
GET  /devices
POST /devices/pixel-6/commands   {"type":"tap","x":540,"y":1200}
GET  /devices/pixel-6/screenshot.jpg
```

Or `just send '{"type":"key","key":"home"}'`. Commands: [docs/protocol.md](docs/protocol.md).

Without the cable: `just tunnel`, then set the app's URL to `wss://<ngrok-host>/ws/device`.

Details: [CLAUDE.md](CLAUDE.md).
