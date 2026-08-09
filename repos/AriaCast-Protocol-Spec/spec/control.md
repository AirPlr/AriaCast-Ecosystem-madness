# AriaCast Control Messages

**Version:** 1.0

Control messages are exchanged over the `/control` WebSocket endpoint and via the `POST /api/command` HTTP endpoint. See [`transport.md`](transport.md) for connection details.

---

## Receiver → Sender: `action`-keyed commands

When a command is triggered (by an external controller, the web UI, or `POST /api/command`), the Receiver sends the following JSON text frame to **all** connected `/control` WebSocket clients, including the Sender:

```json
{"action": "play"}
```

### Command reference

| `action` value | Description | Extra fields |
|---|---|---|
| `play` | Resume or start playback | — |
| `pause` | Pause playback | — |
| `next` | Skip to next track | — |
| `previous` | Go to previous track | — |
| `stop` | Stop playback | — |
| `toggle` | Toggle play/pause state | — |
| `seek` | Seek to a position | `"position_ms": <integer>` |

### Seek example

```json
{"action": "seek", "position_ms": 45000}
```

### Android handling

The Android Sender reads the `"action"` field and maps it case-insensitively to a `MediaCommand` enum:

```
PLAY, PAUSE, TOGGLE, NEXT, PREVIOUS, STOP
```

Unknown `action` values are silently ignored. `seek` triggers `transportControls.seekTo(position_ms)` on the active `MediaController`.

---

## Sender → Receiver: `command`-keyed messages (Python server)

The Python server accepts playback commands using either a `command` key or an `action` key. The Go server does **not** process incoming WebSocket text frames on `/control`; it only sends to clients.

### Playback commands

```json
{"command": "play"}
{"command": "pause"}
{"command": "next"}
{"command": "previous"}
{"command": "stop"}
{"command": "play_pause"}
{"command": "seek", "position_ms": 45000}
{"command": "seek", "value": 45000}
```

`play_pause` toggles the current playback state based on the stored `is_playing` flag. `seek` accepts the position in `position_ms` or `value` (both are integers in milliseconds).

**Response (Python server):**

```json
{"command": "play", "success": true}
{"command": "seek", "position_ms": 45000, "success": true}
```

### Volume commands (Python server only)

Volume commands are only sent by the Android Sender when the target server platform is **not** identified as "Music" (e.g. Music Assistant).

#### Step up/down

```json
{"command": "volume", "direction": "up"}
{"command": "volume", "direction": "down"}
```

#### Query current volume

```json
{"command": "volume", "direction": "get"}
```

#### Set absolute volume (two formats — both accepted)

```json
{"command": "volume", "value": 80}
{"command": "volume_set", "level": 80}
```

Volume is an integer in the range `0`–`100`.

**Response:**

```json
{"command": "volume", "level": 75, "success": true}
```

`"level"` is the resulting volume after the operation (0–100), or `-1` if volume control is unavailable.

---

## HTTP POST /api/command

An HTTP alternative that does not require a persistent WebSocket connection. Used by the web dashboard and external integrations (e.g. Music Assistant).

### Request body

```json
{"action": "play"}
{"action": "pause"}
{"action": "next"}
{"action": "previous"}
```

### Behaviour

1. The Receiver validates that `action` is present.
2. It forwards `{"action": "<value>"}` to any connected `/control` WebSocket client.
3. For `play` and `pause`, the Go server additionally updates the internal `is_playing` state and broadcasts a metadata update to all `/metadata` subscribers.

### Responses

| Status | Condition |
|---|---|
| `200 OK` | Command forwarded successfully |
| `400 Bad Request` | Missing `action` field or invalid JSON |
| `503 Service Unavailable` | No `/control` WebSocket client is connected (Go server) |

---

## Extensibility

- Receivers MUST ignore unknown fields in any control message.
- Unknown `action` or `command` values MUST NOT crash the receiver; they SHOULD be logged.
- New commands MUST use new `action` / `command` string values.
