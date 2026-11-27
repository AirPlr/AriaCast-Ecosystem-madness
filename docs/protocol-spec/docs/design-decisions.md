# AriaCast Design Decisions

**Version:** 1.0

## Transport: WebSocket over TCP

WebSocket was chosen for broad client support and simplicity. All three implementations use the same Gorilla/Ktor/aiohttp WebSocket libraries without custom framing.

## Multiple endpoints instead of multiplexing

Audio, control, stats, and metadata use separate WebSocket paths rather than a multiplexed single connection. This simplifies implementation (no demux logic) and allows clients to subscribe only to what they need (e.g. a web UI subscribes to `/metadata` without opening `/audio`).

## Initial codec: PCM S16LE, fixed frame size

Raw 16-bit PCM at 48 kHz stereo was chosen for zero-complexity implementation. No codec library is required on either side. The fixed 3840-byte (20 ms) frame size eliminates the need for a length-prefix header.

## No frame header

Because the frame size is fixed and negotiated at handshake time, there is no per-frame header. This minimises per-frame overhead and simplifies receiver validation (just a length check).

## Server → Sender handshake (not client-initiated)

The Receiver sends the `READY` handshake first, rather than requiring the Sender to send a `hello`. This means a Sender can simply connect, wait for the first text frame, and begin streaming — no state machine required on the Sender side.

## `action` key for receiver-to-sender commands

Commands forwarded from the Receiver to the Sender use an `action` key (e.g. `{"action": "play"}`). The Python server also accepts a `command` key from external senders for compatibility with multiple client types. The two-key design allows the server to distinguish the source of a message.

## camelCase / snake_case dual acceptance

The Android Kotlin serialization layer uses camelCase (`artworkUrl`, `isPlaying`). Go and Python convention is snake_case (`artwork_url`, `is_playing`). Rather than force one convention, all implementations accept both, and the Go server broadcasts in snake_case.

## UDP broadcast as primary discovery

UDP broadcast to port 12888 is the simplest mechanism that works across all platforms including the Go server (which has no mDNS). mDNS/NSD is an optional enhancement used by the Python server and Android app.
