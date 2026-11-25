# AriaCast Transport

**Version:** 1.0

## Endpoint overview

All endpoints are served on the **streaming port** (default `12889`) over HTTP/WebSocket.

| Path | Protocol | Direction | Purpose |
|---|---|---|---|
| `GET /audio` | WebSocket | Sender → Receiver | Raw PCM audio stream |
| `GET /control` | WebSocket | Bidirectional | Playback control commands |
| `GET /stats` | WebSocket | Receiver → Sender | Buffer/playback statistics |
| `GET /metadata` | WebSocket | Bidirectional | Track metadata push/subscribe |
| `POST /metadata` | HTTP | Sender → Receiver | Track metadata update |
| `POST /api/command` | HTTP | Controller → Receiver | HTTP-based control command |
| `GET /artwork` | HTTP | Receiver → Browser/client | Proxied artwork image |
| `GET /image/artwork` | HTTP | Receiver → Browser/client | Alternate artwork path (Go server) |
| `GET /stream.wav` | HTTP | Receiver → Browser/player | Live WAV audio stream |

---

## /audio — Audio Stream

### Connection

```text
ws://<host>:<port>/audio
```

### Single-client constraint

Both the Python and Go servers accept **one audio Sender at a time**. A second connection attempt while one is active is rejected with **HTTP 403 Forbidden**.

### Handshake

Immediately after the WebSocket upgrade is complete, the Receiver sends a single **JSON text frame** (no frame from the Sender is required first):

```json
{
  "status": "READY",
  "sample_rate": 48000,
  "channels": 2,
  "frame_size": 3840
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"READY"` |
| `sample_rate` | integer | Sample rate in Hz |
| `channels` | integer | Number of audio channels |
| `frame_size` | integer | Expected binary frame size in bytes |

**Sender validation:** The Android Sender accepts the handshake if **either** of the following is true:
- `status == "READY"`, **or**
- `type == "handshake"` (forward-compatibility alias)

Any other response causes the Sender to close the connection and retry.
The handshake must arrive within **3 seconds**; timeout causes disconnect and retry.

### Audio frames

After a successful handshake, the Sender streams **WebSocket binary frames**. Each frame is exactly `frame_size` bytes (normally `3840`) of raw PCM audio. There is no per-frame header. See [`audio.md`](audio.md) for the PCM layout.

The Receiver ignores all text frames received after the handshake. Only binary frames carry audio data.

---

## /control — Control Channel

### Connection

```text
ws://<host>:<port>/control
```

### Handshake (Python server only)

The **Python server** immediately sends an initial status frame after the WebSocket upgrade:

```json
{
  "status": "READY",
  "volume_available": true,
  "current_volume": 75
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"READY"` |
| `volume_available` | boolean | Whether OS volume control is available |
| `current_volume` | integer | Current system volume level (0–100), or `-1` if unavailable |

The **Go server** does not send any handshake message on `/control`.

### Multiple clients

Both servers allow multiple simultaneous `/control` WebSocket connections. Commands received from one client are forwarded to all other connected clients.

### Messages: Receiver → Sender (`action`-keyed)

The Receiver sends **JSON text frames** with an `action` key to all connected `/control` clients:

```json
{"action": "play"}
{"action": "pause"}
{"action": "next"}
{"action": "previous"}
{"action": "stop"}
{"action": "toggle"}
{"action": "seek", "position_ms": 12345}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | Command to execute (see table below) |
| `position_ms` | integer | only for `seek` | Target playback position in milliseconds |

**Supported `action` values:**

| `action` | Description |
|---|---|
| `play` | Resume / start playback |
| `pause` | Pause playback |
| `next` | Skip to next track |
| `previous` | Go to previous track |
| `stop` | Stop playback |
| `toggle` | Toggle play/pause |
| `seek` | Seek to `position_ms` |

The Android app maps the incoming `action` string to a `MediaCommand` enum (case-insensitive). Unknown `action` values are silently ignored.

### Messages: Sender → Receiver (`command`-keyed — Python server)

The Android app and external controllers send playback commands with a `command` key:

```json
{"command": "play"}
{"command": "pause"}
{"command": "next"}
{"command": "previous"}
{"command": "stop"}
{"command": "play_pause"}
{"command": "seek", "position_ms": 12345}
{"command": "seek", "value": 12345}
```

The Python server also accepts `action`-keyed messages interchangeably:

```json
{"action": "play"}
{"action": "pause"}
{"action": "next"}
{"action": "previous"}
{"action": "seek", "position_ms": 12345}
```

**Response from Python server:**

```json
{"command": "play", "success": true}
{"action": "play", "success": true}
{"command": "seek", "position_ms": 12345, "success": true}
```

### Messages: Sender → Receiver (volume — Python server)

The Android app sends volume control commands to the Python server. Volume commands are disabled for receivers identified as a "Music" platform.

```json
{"command": "volume", "direction": "up"}
{"command": "volume", "direction": "down"}
{"command": "volume", "direction": "get"}
{"command": "volume", "value": 80}
{"command": "volume_set", "level": 80}
```

| Field | Type | Description |
|---|---|---|
| `command` | string | `"volume"` or `"volume_set"` |
| `direction` | string | `"up"`, `"down"`, or `"get"` (used with `command: "volume"`) |
| `value` | integer | Absolute volume level 0–100 (used with `command: "volume"`) |
| `level` | integer | Absolute volume level 0–100 (used with `command: "volume_set"`) |

**Response:**

```json
{"command": "volume", "level": 75, "success": true}
```

If volume control is unavailable, `"level"` is `-1` and `"success"` is `false`.

---

## /stats — Statistics

### Connection

```text
ws://<host>:<port>/stats
```

### Server push (Python server)

The Python server pushes a stats **JSON text frame** every **1 second**:

```json
{
  "receivedFrames": 1500,
  "playedCallbacks": 1480,
  "underruns": 3,
  "overruns": 0,
  "queuedFrames": 25,
  "bufferLevel": "25.0%"
}
```

| Field | Type | Description |
|---|---|---|
| `receivedFrames` | integer | Total binary audio frames received since connect |
| `playedCallbacks` | integer | Number of audio hardware callback invocations |
| `underruns` | integer | Buffer underrun count (silence was inserted) |
| `overruns` | integer | Buffer overrun count (frames were discarded) |
| `queuedFrames` | integer | Frames currently queued for playback |
| `bufferLevel` | string | Buffer fill as a percentage string, e.g. `"25.0%"` |

The Go server does not currently push stats.

### Android stats reading

The Android app reads these specific keys from incoming stats frames:

| Key | Description |
|---|---|
| `receivedFrames` | Received frame counter |
| `bufferedFrames` | Buffered frame counter (defaults to `0` if key absent) |
| `droppedFrames` | Dropped frame counter (defaults to `0` if key absent) |

---

## /metadata — Metadata Channel

See [`metadata.md`](metadata.md) for the complete metadata protocol.

---

## POST /api/command — HTTP Control

An HTTP alternative to the WebSocket control channel, used by the web dashboard and external integrations.

### Request

```
POST http://<host>:<port>/api/command
Content-Type: application/json
```

```json
{"action": "play"}
{"action": "pause"}
{"action": "next"}
{"action": "previous"}
```

| Field | Type | Description |
|---|---|---|
| `action` | string | Playback command to forward |

### Response

- **HTTP 200 OK** — command was forwarded to a connected `/control` WebSocket client
- **HTTP 503 Service Unavailable** — no `/control` client is currently connected (Go server)
- **HTTP 400 Bad Request** — missing or invalid JSON

When a `play` or `pause` action is received, the Go server also updates the internal `is_playing` metadata state and broadcasts the change to all `/metadata` WebSocket subscribers.

---

## Connection lifecycle

1. Sender establishes TCP connection
2. WebSocket upgrade (HTTP `GET` with `Upgrade: websocket`)
3. Receiver sends handshake (on `/audio` and `/control` for Python server)
4. Active session
5. Either side may close the WebSocket at any time
6. Sender reconnects with exponential backoff (see [`timing.md`](timing.md))
