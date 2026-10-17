# Archangel

Lets an AI agent operate a real Android phone over the network.

## Setup

Needs `just`, `uv`, JDK 17, Android SDK. Phone needs USB debugging.

```sh
cp .env.example .env     # set ARCHANGEL_TOKEN
just relay               # keep running
just reverse
just bridge-install
```

Open **Archangel Bridge** on the phone. It should say **Connected**.

## Use

Send `Authorization: Bearer $ARCHANGEL_TOKEN` with every request.

```
GET  /devices
POST /devices/pixel-6/commands   {"type":"tap","x":540,"y":1200}
GET  /devices/pixel-6/screenshot.jpg
```

Or `just send '{"type":"key","key":"home"}'`. Commands: [docs/protocol.md](docs/protocol.md).

## Without the cable

`just tunnel`, then set the app's URL to `wss://<ngrok-host>/ws/device`.

Details: [CLAUDE.md](CLAUDE.md).
