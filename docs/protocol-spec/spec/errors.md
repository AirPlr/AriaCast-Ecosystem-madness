# AriaCast Error Handling

**Version:** 1.0

There is no unified error envelope in AriaCast. Errors are communicated differently depending on the endpoint.

---

## HTTP errors

| Endpoint | Condition | HTTP status |
|---|---|---|
| `GET /audio` | A second sender connects while one is active | `403 Forbidden` |
| `GET /control` | A second connection when only one is allowed (Go server) | `403 Forbidden` |
| `POST /api/command` | Missing `action` field or malformed JSON | `400 Bad Request` |
| `POST /api/command` | No `/control` WebSocket client connected (Go server) | `503 Service Unavailable` |
| `POST /api/command` | Failed to write to connected control client | `500 Internal Server Error` |
| `GET /artwork` | No artwork cached | `404 Not Found` |
| `POST /metadata` | Invalid JSON body | `400 Bad Request` |

---

## /audio WebSocket errors

| Condition | Behaviour |
|---|---|
| Server does not send handshake within 3 seconds | Sender disconnects and retries (Android) |
| Handshake `status != "READY"` and `type != "handshake"` | Sender disconnects and retries (Android) |
| Received frame length != 3840 bytes | Python server logs a warning and discards the frame |
| `AudioRecord` error (negative read result) | Android Sender terminates the session |

---

## /control WebSocket errors

JSON text frame sent by the Python server when a command cannot be processed:

```json
{"error": "Invalid JSON"}
{"error": "Unknown command: <value>"}
{"error": "position_ms must be a number"}
{"error": "Invalid volume format. Provide 'value' or 'direction'"}
{"error": "level must be a number (0-100)"}
```

Unknown `action` values received by the Android Sender are silently ignored (no error is generated).

---

## /metadata WebSocket errors

JSON text frame sent by the Python server:

```json
{"type": "error", "message": "Invalid JSON"}
```

---

## General rules

- Implementations MUST NOT crash on unknown JSON fields.
- All JSON parsing MUST be tolerant of additional unknown keys (`additionalProperties` allowed).
- Errors that close the WebSocket trigger the Sender's reconnect logic with exponential backoff.
- The Go server does not emit JSON error frames on WebSocket connections; it simply closes the connection.
