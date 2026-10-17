# Protocol

The phone dials out to the relay over WebSocket. Callers (Postman, the agent) talk to the relay over HTTP.
All WebSocket frames are JSON text. All endpoints need `Authorization: Bearer $ARCHANGEL_TOKEN`.

```
caller ──HTTP──▶ relay ◀──WebSocket── phone
```

## Delivery semantics

- **Request/response.** Every command carries an `id`; the phone answers with the same `id`.
- **At most once, never queued.** If the phone is offline the relay fails immediately. A stale tap is worse than an error.
- **Retry with the same `id`.** The phone remembers its last 64 results and replays them for a repeated `id` instead of executing twice.
- **When in doubt, look.** After a timeout or disconnect, take a screenshot before acting again.

## Phone → relay

On connect, before anything else:

```json
{"type": "hello", "device": "pixel-6", "screen": {"width": 1080, "height": 2400, "density": 2.625}, "version": "0.1.0"}
```

A second connection with the same `device` name replaces the first.

Results:

```json
{"id": "…", "ok": true, "data": {}}
{"id": "…", "ok": false, "error": {"code": "not_found", "message": "No launchable app: com.example"}}
```

Error codes: `invalid_command`, `not_found`, `failed`, `internal`.

## Relay → phone

`{"id": "…", "type": "<command>", …params}`. Coordinates are physical screen pixels, the same pixels as a screenshot.

| type         | params                                       | data                                         |
|--------------|----------------------------------------------|----------------------------------------------|
| `screenshot` |                                              | `{"width", "height", "jpeg": "<base64>"}`    |
| `tap`        | `x`, `y`, `durationMs`? (default 50)         |                                              |
| `swipe`      | `x1`, `y1`, `x2`, `y2`, `durationMs`? (300)  |                                              |
| `type`       | `text` (replaces the focused field's text)   |                                              |
| `key`        | `key`: `back` `home` `recents` `notifications` `quick_settings` `lock` |                    |
| `launch`     | `package`                                    |                                              |
| `ui_tree`    |                                              | `{"windows": [Node]}`                        |

`Node`: `{"class", "text"?, "desc"?, "id"?, "bounds": [left, top, right, bottom], "flags"?: ["clickable", "editable", "scrollable", "focused"], "children"?: [Node]}`. Empty fields are omitted.

## HTTP

| method & path                         | behaviour                                                     |
|---------------------------------------|---------------------------------------------------------------|
| `GET /health`                         | `200`, no auth                                                |
| `GET /devices`                        | connected devices and their hello                             |
| `POST /devices/{device}/commands`     | body is a command; `id` optional. Returns the phone's result  |
| `GET /devices/{device}/screenshot.jpg`| the screen as an image, or `502` with the phone's error       |

`POST` returns `200` with the phone's result (which may itself be `ok: false`), or:

| status | code           | meaning                                            |
|--------|----------------|----------------------------------------------------|
| `401`  |                | bad token                                          |
| `422`  |                | body is not a command                              |
| `502`  | `disconnected` | phone dropped mid-command; outcome unknown         |
| `503`  | `offline`      | phone not connected; nothing was sent              |
| `504`  | `timeout`      | no answer within 15 s; outcome unknown             |
