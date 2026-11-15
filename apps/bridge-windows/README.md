# Windows bridge

Lets Archangel operate a Windows PC, as the Android bridge does the phone: it dials the relay and runs its commands ([protocol](../../docs/protocol.md)). Installed as a service, it runs from boot whoever's signed in, and can unlock the lock screen with a PIN kept on the PC.

## Install

1. Download `archangel-bridge.zip` from the [Windows bridge release](https://github.com/pTinosq/archangel/releases/tag/bridge-windows) and unzip it anywhere (Downloads is fine).
2. Run `archangel-bridge.exe` in it and say yes to the administrator prompt. It isn't signed: SmartScreen may say "Windows protected your PC"; More info → Run anyway. In the window: the relay's URL (`wss://<relay>/ws/device`) and token, a name for the PC, and its PIN (optional: without one it can't unlock).
3. **Save and start.** It copies itself (the folder) to `C:\Program Files\Archangel`, installs the `ArchangelBridge` service (automatic, restarts on failure) and starts it. The window shows when it's connected.

To change a setting, run `C:\Program Files\Archangel\archangel-bridge.exe` (or any copy) again. To update, unzip a newer download, run it and Save. **Stop and remove** takes the service away (settings are kept in `C:\ProgramData\Archangel`, readable only by administrators and SYSTEM; the PIN encrypted for the machine).

For the PC to be reachable, in Settings: never sleep (or set up waking, below), and no sign-in after waking from sleep is fine (the bridge unlocks it). Multiple displays: it uses the primary one.

**Waking (optional).** A sleeping PC can't dial the relay. The phone, on the same network, can wake it with Wake-on-LAN: in the BIOS, enable Wake on LAN; in Device Manager, the network adapter's Power Management: "Allow this device to wake the computer" and "Only allow a magic packet"; on its Advanced tab, "Wake on Magic Packet" on. Wired works; Wi-Fi rarely does. Turning off Fast Startup (Control Panel → Power Options) helps waking from shutdown.

## How it works

| File | Role |
|---|---|
| `protocol.py` | Wire types, the contract in `docs/protocol.md`. |
| `executor.py` | Decode, run serially, never raise. Replays replies for repeated ids (last 64). |
| `device.py` / `windows.py` | The PC behind an interface (tests use a fake); `WindowsDevice` does each command. |
| `win32.py` | The Windows calls: GDI capture, `SendInput`, following the input desktop, processes as the signed-in user. |
| `keys.py` | Key names and chords to virtual-key codes. |
| `uia.py` | The foreground window's UI Automation tree. |
| `connection.py` | WebSocket to the relay: 15 s pings, 1–30 s jittered backoff. |
| `config.py` | Settings: Dev and Prod relays, which is on, the PC's name, the PIN. One JSON file. |
| `service.py` | The service: starts the bridge as SYSTEM in the session at the screen and keeps it there; install and remove. |
| `settings.py` | The settings window. |

The lock screen and administrator (UAC) prompts are a separate desktop, `Winlogon`, that only SYSTEM can open. A service has no screen (session 0), so it starts the bridge as SYSTEM in the console session, where it switches its input thread onto whichever desktop has input before each command. Apps it opens and the Start menu it lists are the signed-in user's (their token, from the session), never SYSTEM's.

## Develop

```sh
just bridge-windows-test   # on any OS: everything but the real desktop
```

Logs: `C:\ProgramData\Archangel\logs` (`service.log`, `bridge.log`, `settings.log`); an empty file named `trace` in `C:\ProgramData\Archangel` adds every thread's stack every 15 s, for a hang. Every failure is logged rather than shown: the windowed exe would show it in a dialog, and under the service no one could close it.

On Windows, `uv run python -m bridge run --config bridge.json` runs it in the foreground as you (no lock screen), with a settings file like `{"mode": "dev", "dev": {"url": "ws://<mac>:8080/ws/device", "token": "…"}, "device": "desk"}`. CI (`.github/workflows/bridge-windows.yml`) runs every test on a Windows runner's real desktop, builds it with PyInstaller (a folder, not one file: a one-file exe runs the app as a child process, which a service can't be), runs it against the relay in the foreground and as the service (locking the runner's screen and unlocking it with the bridge's secret), and publishes it from `main`.
