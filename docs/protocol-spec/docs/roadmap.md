# AriaCast Roadmap

## Currently implemented (v1.0)

- UDP broadcast discovery on port 12888
- mDNS/NSD discovery (`_audiocast._tcp`) — Python server and Android app
- WebSocket audio streaming on `/audio` with fixed-size raw PCM frames
- WebSocket control channel on `/control` (action-keyed + command-keyed)
- WebSocket stats on `/stats` (Python server)
- WebSocket metadata subscription on `/metadata`
- HTTP POST metadata at `/metadata`
- HTTP artwork proxy at `/artwork`
- HTTP WAV stream at `/stream.wav`
- Named pipe bridge for Music Assistant integration (Go server)
- System volume control via OS APIs (Python server, Windows)
- Media session integration and notification listener (Android app)

## Planned protocol work

- Add authenticated pairing and session auth (currently no auth)
- Define optional TLS profile (`wss://`, `https://`)
- Add negotiated compressed codecs (Opus) via handshake
- Add multiroom synchronisation primitives
- Standardise stats payload keys across Python and Go servers
- Define a versioned mDNS TXT `version` format
