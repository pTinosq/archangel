# Archangel

Lets an AI agent operate a real Android phone over the network: no cable, no ADB, no root.

```sh
cp .env.example .env    # set ARCHANGEL_TOKEN
just relay              # terminal 1
just reverse && just bridge-install && just bridge-enable
just send '{"type":"launch","package":"com.android.settings"}'
just screenshot
```

The protocol is in [docs/protocol.md](docs/protocol.md); everything else is in [CLAUDE.md](CLAUDE.md).
