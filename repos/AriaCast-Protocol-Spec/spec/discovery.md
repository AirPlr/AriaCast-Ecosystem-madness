# AriaCast Discovery

**Version:** 1.0

## Ports

| Port | Protocol | Purpose |
|---|---|---|
| `12888` | UDP | Broadcast discovery |
| `12889` | TCP | WebSocket streaming (default) |

---

## 1. UDP Broadcast Discovery

All implementations support UDP broadcast discovery. This is the primary discovery mechanism.

### Request

The Sender broadcasts a UTF-8 UDP datagram to `255.255.255.255:12888`:

```text
DISCOVER_AUDIOCAST
```

No newline is required; the Receiver trims whitespace before comparing.

### Response

The Receiver sends a UDP datagram back to the Sender's source address and port:

```json
{
  "server_name": "AudioCast Speaker",
  "ip": "192.168.1.100",
  "port": 12889,
  "samplerate": 48000,
  "channels": 2
}
```

| Field | Type | Description |
|---|---|---|
| `server_name` | string | Human-readable receiver display name |
| `ip` | string | Receiver's outbound LAN IP address |
| `port` | integer | WebSocket streaming port (normally `12889`) |
| `samplerate` | integer | Audio sample rate in Hz (normally `48000`) |
| `channels` | integer | Channel count (normally `2`) |

Unknown fields in the response MUST be ignored by the Sender.

### Android retry behaviour

The Android Sender:
- Broadcasts to `255.255.255.255:12888`
- Waits up to 2 seconds for responses per attempt
- Retries up to **5 times** with a **3-second delay** between attempts

---

## 2. mDNS / DNS-SD Discovery

Supported by the **Python server** (via Zeroconf) and the **Android app** (via Android NSD). Not implemented by the Go server.

### Service type

```text
_audiocast._tcp
```

### Instance name

The receiver's human-readable name, e.g. `AudioCast Speaker`.

### Port

The WebSocket streaming port (normally `12889`).

### TXT record properties

| Key | Example value | Type | Description |
|---|---|---|---|
| `version` | `1.0` | string | Server software version |
| `samplerate` | `48000` | string (integer encoded) | Audio sample rate in Hz |
| `channels` | `2` | string (integer encoded) | Audio channel count |
| `platform` | `Windows` | string | Optional: host platform identifier |
| `codecs` | `PCM` | string (comma-separated) | Optional: supported codec list |

All TXT values are strings. Receivers MUST encode numeric values as decimal strings.
The Android NSD client reads all five keys; `platform` and `codecs` are optional and may be absent.

### Endpoint construction

After mDNS resolution the Sender builds the WebSocket endpoint:

```text
ws://<resolved-host>:<port>/audio
```

---

## 3. Manual connection

If discovery is unavailable, the user may enter a host and port manually. The Sender constructs:

```text
ws://<ip>:<port>/audio
```

Default port: `12889`.
Manual servers added via the Android UI are assigned `version = "1.0"`, `codecs = ["pcm"]`, `sampleRate = 48000`, `channels = 2`, and `platform = "Manual"`.
