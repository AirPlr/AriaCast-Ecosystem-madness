# Minimal Sender (Client) Outline

Steps to implement a minimal AriaCast Sender:

1. **Discover** the Receiver:
   - Broadcast `"DISCOVER_AUDIOCAST"` (UTF-8) to `255.255.255.255:12888` (UDP).
   - Wait for a JSON response: `{"server_name":"...","ip":"...","port":12889,"samplerate":48000,"channels":2}`.
   - Alternatively, browse mDNS for `_audiocast._tcp` to find NSD-advertised receivers.

2. **Connect** to the audio endpoint:
   - Open `ws://<host>:<port>/audio`.

3. **Handshake**:
   - Wait for a JSON text frame from the server.
   - Validate: `status == "READY"` (or `type == "handshake"` as a forward-compat alias).
   - The frame includes `sample_rate`, `channels`, and `frame_size`.

4. **Stream audio**:
   - Capture audio at 48000 Hz, stereo, PCM S16LE.
   - Read exactly `frame_size` bytes (3840) per frame.
   - Send each frame as a **binary** WebSocket message (no header — raw PCM only).

5. **Handle control commands** (optional):
   - Connect to `ws://<host>:<port>/control`.
   - Listen for JSON text frames: `{"action":"play"}`, `{"action":"pause"}`, etc.
   - Execute the action on your local media session.

6. **Push metadata** (optional):
   - When the playing track changes, POST to `http://<host>:<port>/metadata`:
     ```json
     {"data":{"title":"...","artist":"...","album":"...","artworkUrl":"...","durationMs":240000,"positionMs":0,"isPlaying":true}}
     ```

7. **Reconnect** on disconnect with exponential backoff starting at 1 second.
