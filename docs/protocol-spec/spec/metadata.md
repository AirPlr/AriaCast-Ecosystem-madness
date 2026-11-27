# AriaCast Metadata

**Version:** 1.0

Metadata describes the currently playing track and playback state. It flows from the Sender (Android app) to the Receiver (server) and is then broadcast to all `/metadata` WebSocket subscribers.

---

## Metadata object

The canonical metadata object contains the following fields:

| Field | Type | Description |
|---|---|---|
| `title` | string or null | Track title |
| `artist` | string or null | Artist name |
| `album` | string or null | Album name |
| `artwork_url` | string or null | URL of the artwork image |
| `duration_ms` | integer or null | Track duration in milliseconds |
| `position_ms` | integer or null | Current playback position in milliseconds |
| `is_playing` | boolean | Whether playback is currently active |

### Key naming convention

The Android Sender serialises the metadata using **camelCase** key names:

| Android (camelCase) | Server broadcast (snake_case) |
|---|---|
| `artworkUrl` | `artwork_url` |
| `durationMs` | `duration_ms` |
| `positionMs` | `position_ms` |
| `isPlaying` | `is_playing` |

The Go server stores and broadcasts in **snake_case**. The Python server stores and re-broadcasts whichever key names it received, and additionally accepts both `artworkUrl` / `artwork_url` and `isPlaying` / `is_playing` from incoming updates.

**Interoperability rule:** All implementations MUST accept **both** the camelCase and snake_case variants for every metadata field. Implementations MUST NOT reject a metadata object because it uses the "wrong" casing convention.

---

## HTTP POST /metadata — Sender pushes metadata

### Request

```
POST http://<host>:<port>/metadata
Content-Type: application/json
```

The Android app wraps the metadata object inside a `"data"` envelope:

```json
{
  "data": {
    "title": "Night Drive",
    "artist": "Aria Ensemble",
    "album": "Midnight Sessions",
    "artworkUrl": "http://192.168.1.50:8090/artwork.jpg",
    "durationMs": 240000,
    "positionMs": 5000,
    "isPlaying": true
  }
}
```

The Receiver unwraps the `"data"` envelope if present. A flat object **without** the `"data"` wrapper is also accepted by both servers.

### Response (Python server)

```json
{"success": true}
```

### Response (Go server)

HTTP 200 with an empty body.

---

## WebSocket GET /metadata — Subscribe to metadata updates

```text
ws://<host>:<port>/metadata
```

### On connect: initial state push

Immediately after the WebSocket upgrade, the Receiver sends the current metadata state as a JSON text frame:

```json
{
  "type": "metadata",
  "data": {
    "title": "Night Drive",
    "artist": "Aria Ensemble",
    "album": "Midnight Sessions",
    "artwork_url": "/artwork",
    "duration_ms": 240000,
    "position_ms": 5000,
    "is_playing": true
  }
}
```

If no metadata has been received yet, `"data"` will be an empty object `{}`.

### Server → subscriber: broadcast on change

Whenever metadata is updated (via HTTP POST, WebSocket update, or a play/pause command that changes `is_playing`), the server sends the same message format to **all** connected `/metadata` WebSocket clients:

```json
{
  "type": "metadata",
  "data": {
    "title": "...",
    "artist": "...",
    "album": "...",
    "artwork_url": "...",
    "duration_ms": 240000,
    "position_ms": 5000,
    "is_playing": true
  }
}
```

### Client → server: push an update

```json
{
  "type": "update",
  "data": {
    "title": "New Track",
    "artist": "New Artist",
    "isPlaying": true
  }
}
```

Partial updates are accepted — only the provided fields are merged into the current state.

**Server reply:**

```json
{"type": "ack", "success": true}
```

### Client → server: request current state

```json
{"type": "get"}
```

**Server reply:**

```json
{
  "type": "metadata",
  "data": { ... }
}
```

### Client → server: clear all metadata

```json
{"type": "clear"}
```

**Server reply:**

```json
{"type": "ack", "success": true}
```

### Error response

```json
{"type": "error", "message": "Invalid JSON"}
```

---

## Artwork

### How artwork flows

1. The **Android app** obtains artwork from the Android `MediaMetadata` APIs.
   - If a `Bitmap` is available, it is compressed to JPEG (quality 80) and hosted on a local HTTP server on **port 8090**.
   - The metadata sent to the receiver includes `artworkUrl` pointing to `http://<phone-ip>:8090/artwork.jpg`.
2. The **Python server** detects an HTTP(S) `artworkUrl` in the incoming metadata, downloads the image, stores it in memory, and updates the metadata's `artwork_url` to the local path `/artwork`. It then broadcasts the updated metadata.
3. The **Go server** downloads the artwork from `artworkUrl` in the background and serves it at both `/artwork` and `/image/artwork`.

### GET /artwork

Returns the currently cached artwork image.

- **Content-Type:** `image/jpeg` (Go server always), or auto-detected from magic bytes (`image/jpeg`, `image/png`, `image/gif`, `image/webp`) by the Python server.
- **Cache-Control:** `max-age=3600` (Python server).
- **HTTP 404** if no artwork is available.

---

## Periodic metadata refresh

The Android app re-sends the current metadata to the server every **10 seconds** to ensure metadata remains in sync after any server restart or reconnect. Metadata is also re-sent immediately after the audio WebSocket reconnects.

---

## Position tracking

The Android app calculates `positionMs` in real time:

```
position = last_reported_position
         + (elapsed_realtime_ms - last_position_update_time_ms) × playback_speed
```

This is updated and sent every **1 second** while playback is active.
