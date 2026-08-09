# Minimal Receiver (Server) Outline

Steps to implement a minimal AriaCast Receiver:

1. **Advertise** via UDP discovery:
   - Listen for UDP datagrams on port `12888`.
   - On receiving `"DISCOVER_AUDIOCAST"`, reply with:
     ```json
     {"server_name":"My Receiver","ip":"<your-ip>","port":12889,"samplerate":48000,"channels":2}
     ```
   - Optionally also advertise via mDNS/DNS-SD as `_audiocast._tcp` with TXT keys `version`, `samplerate`, `channels`.

2. **Accept** audio connections:
   - Listen for WebSocket connections on `GET /audio` (port `12889`).
   - Accept only **one** audio client at a time; return HTTP 403 if a client is already connected.

3. **Send handshake**:
   - Immediately after WebSocket upgrade, send a JSON text frame:
     ```json
     {"status":"READY","sample_rate":48000,"channels":2,"frame_size":3840}
     ```

4. **Receive audio frames**:
   - Accept binary WebSocket messages.
   - Each message is exactly 3840 bytes of raw PCM S16LE stereo audio (no header).
   - Discard any message that is not exactly 3840 bytes.
   - Pass samples to audio output.

5. **Accept control connections** (optional):
   - Listen on `GET /control`.
   - To send a command to the Sender, write: `{"action":"play"}` / `{"action":"pause"}` etc.
   - To receive commands from external controllers, read incoming text frames (Python server uses `command` key, but also accepts `action` key).

6. **Accept metadata** (optional):
   - `POST /metadata`: parse JSON body (unwrap `data` envelope if present), store fields, broadcast to `/metadata` WebSocket subscribers.
   - `GET /metadata` (WebSocket): on connect, send `{"type":"metadata","data":{...}}`. On `{"type":"update","data":{...}}` from client, merge and broadcast. Reply with `{"type":"ack","success":true}`.

7. **Serve artwork** (optional):
   - If `artwork_url` in received metadata is an HTTP URL, download and cache it.
   - Serve the bytes at `GET /artwork`.
