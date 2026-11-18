# Protocol

A device's bridge (the Android app, the Windows service) dials out to the relay over WebSocket. Callers (Postman, the agent) talk to the relay over HTTP. Every bridge speaks this one contract; what differs by platform is marked.
All WebSocket frames are JSON text. All endpoints need `Authorization: Bearer $ARCHANGEL_TOKEN`.

```
caller ──HTTP──▶ relay ◀──WebSocket── device
```

## Delivery semantics

- **Request/response.** Every command carries an `id`; the device answers with the same `id`.
- **At most once, never queued.** If the device is offline the relay fails immediately. A stale tap is worse than an error.
- **Retry with the same `id`.** The device remembers its last 64 results and replays them for a repeated `id` instead of executing twice.
- **When in doubt, look.** After a timeout or disconnect, take a screenshot before acting again.
- **One at a time.** A device runs its commands serially, in arrival order.

## Device → relay

On connect, before anything else:

```json
{"type": "hello", "device": "pixel-6", "platform": "android", "screen": {"width": 1080, "height": 2400, "density": 2.625}, "version": "0.1.0"}
{"type": "hello", "device": "desk", "platform": "windows", "screen": {"width": 2560, "height": 1440, "density": 1.25}, "version": "0.1.0", "wake": {"mac": "a8:a1:59:12:34:56"}}
```

- `platform`: `android` or `windows`. A hello without one is `android` (bridges older than the field).
- `screen`: physical pixels; on Windows, the primary display. `density` is pixels per density-independent pixel (Windows: scaling, 1.25 = 125%).
- `wake` (optional): the network card to wake it by, for Wake-on-LAN, while it's asleep.

A second connection with the same `device` name replaces the first.

Results:

```json
{"id": "…", "ok": true, "data": {}}
{"id": "…", "ok": false, "error": {"code": "not_found", "message": "No launchable app: com.example"}}
```

Error codes: `invalid_command` (malformed, or not a command this platform has), `not_found`, `failed`, `internal`.

## Relay → device

`{"id": "…", "type": "<command>", …params}`. Coordinates are physical screen pixels, the same pixels as a full-scale screenshot.

| type         | params                                       | data                                         | platforms |
|--------------|----------------------------------------------|----------------------------------------------|-----------|
| `screenshot` | `scale`? in (0, 1] (default 1), `quality`? JPEG 1–100 (80) | `{"width", "height", "jpeg": "<base64>"}` (the scaled size) | all |
| `tap`        | `x`, `y`, `durationMs`? (default 50); Windows also `button`? `left` (default) or `right`, `count`? 1 (default) or 2 | | all (Windows: a mouse click) |
| `swipe`      | `x1`, `y1`, `x2`, `y2`, `durationMs`? (300)  |                                              | all (Windows: a drag with the left button) |
| `scroll`     | `x`, `y`, `dx`?, `dy`? (mouse wheel notches; positive moves the view right or down, revealing what's below) | | windows |
| `type`       | `text`                                       |                                              | all |
| `key`        | `key`                                        |                                              | all |
| `launch`     | `package`: an app's id from `apps`           |                                              | all |
| `apps`       |                                              | `{"apps": [{"label", "package"}]}`         | all |
| `ui_tree`    |                                              | `{"windows": [Node]}`                        | all |
| `unlock`     |                                              |                                              | windows |
| `powershell` | `script`: PowerShell, 1–6,000 characters     | `{"exitCode", "stdout", "stderr"}`           | windows |
| `location`   | `lat`, `lon` (degrees) to fake GPS there, until the next `location`; neither to stop | | android |
| `wake`       | `mac`: `aa:bb:cc:dd:ee:ff` (or with `-`)     |                                              | android |

**`type`.** Android replaces the focused field's text (pasting if the field refuses). Windows types at the cursor, as the keyboard would, and line breaks are typed as Shift+Enter, so typing never submits anything.

**`key`.** Android: `back` `home` `recents` `notifications` `quick_settings` `lock` `enter` (the focused field's IME action). Windows: a key or a chord joined by `+`, e.g. `enter`, `ctrl+l`, `alt+f4`, `ctrl+shift+t`, `win`. Modifiers `ctrl` `alt` `shift` `win`; keys `a`–`z`, `0`–`9`, punctuation, `enter` `esc` `tab` `space` `backspace` `delete` `insert` `up` `down` `left` `right` `home` `end` `pageup` `pagedown` `f1`–`f24` `menu` `printscreen` `volumeup` `volumedown` `mute` `playpause`, and `lock` (locks the PC).

**`launch` and `apps`.** Android: an app's package. Windows: its Start menu id (an AppUserModelID or a path, from `Get-StartApps`), opened as the signed-in user.

**`ui_tree`.** Android: every window. Windows: the foreground window, from UI Automation, up to 30 levels deep and 500 nodes.

`Node`: `{"class", "text"?, "desc"?, "id"?, "bounds": [left, top, right, bottom], "flags"?: ["clickable", "editable", "scrollable", "focused"], "children"?: [Node]}`. Empty fields are omitted. On Windows `class` is the control type (`Button`, `Edit`), `text` its name, `desc` its help text, `id` its automation id.

**`unlock` (Windows).** Types the PIN or password kept in the bridge's own settings on the lock screen (the caller never sees it), and succeeds once the desktop is back. `failed` when none is set, when the bridge isn't running as its service (only the service can reach the lock screen), on an administrator (UAC) prompt rather than the lock screen, or when it's still locked afterwards. Already unlocked: succeeds at once.

**`powershell` (Windows).** Runs `script` in Windows PowerShell as the user signed in at the screen (never as the service's SYSTEM), without administrator rights, in their home folder; what it opens appears on their desktop. It runs while the PC is locked too (what it opens is behind the lock screen until it's unlocked); `failed` when nobody is signed in. After 10 s it's stopped, with everything it started, and `exitCode` is `null`; otherwise it's the script's exit code. A failing script is still `ok`: its exit code and `stderr` say so. Each of `stdout` and `stderr` is cut after 32,000 bytes. Not resent after a lost connection: it may have changed something.

**Locked (Windows).** While the PC is locked, or an administrator prompt is up, the service sees and works that screen like any other. Run outside the service, the bridge can't, and answers `failed` saying so.

**`location` (Android)** needs the bridge chosen as the mock location app (Developer options), else `failed`. Apps see the faked position from GPS and network location; it's dropped if the service stops.

**`wake` (Android)** broadcasts a Wake-on-LAN magic packet on the phone's network (UDP ports 9 and 7), to wake a PC asleep on the same network.

## HTTP

| method & path                         | behaviour                                                     |
|---------------------------------------|---------------------------------------------------------------|
| `GET /health`                         | `200`, no auth                                                |
| `GET /devices`                        | connected devices and their hello                             |
| `POST /devices/{device}/commands`     | body is a command; `id` optional. Returns the device's result |
| `GET /devices/{device}/screenshot.jpg`| the screen as an image, or `502` with the device's error      |

`POST` returns `200` with the device's result (which may itself be `ok: false`), or:

| status | code           | meaning                                            |
|--------|----------------|----------------------------------------------------|
| `401`  |                | bad token                                          |
| `422`  |                | body is not a command                              |
| `502`  | `disconnected` | device dropped mid-command; outcome unknown        |
| `503`  | `offline`      | device not connected; nothing was sent             |
| `504`  | `timeout`      | no answer within 15 s; outcome unknown             |
