# AriaCast Server (Go)

> High-performance, low-latency PCM audio streaming server written in Go

![Go Version](https://img.shields.io/badge/Go-1.21+-00ADD8?style=flat&logo=go)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

AriaCast Server (Go) is a lightweight, high-performance reimplementation of the [Python AriaCast Server](https://github.com/AirPlr/Ariacast-server-python). It transforms any device into a network audio receiver with real-time metadata synchronization, remote control, and Music Assistant integration.

**Why Go?** This implementation offers:
- ⚡ **Better Performance** - Lower CPU usage and memory footprint
- 🚀 **Single Binary** - No dependencies, just run the executable
- 🌍 **Easy Deployment** - Pre-compiled binaries for all platforms
- 🔧 **Music Assistant Integration** - Native pipe support for seamless integration

## 🔗 Related Projects

- **[AriaCast Server (Python)](https://github.com/AirPlr/Ariacast-server-python)** - Original implementation with web dashboard and local audio playback
- **[AriaCast Music Assistant Plugin](https://github.com/AirPlr/AriaCast-Receiver-MusicAssistant)** - Music Assistant provider integration

## ✨ Features

### 🎵 Core Audio Streaming
- **WebSocket Streaming**: Low-latency raw PCM transmission (48kHz/16-bit stereo)
- **HTTP WAV Stream**: `/stream.wav` endpoint for browser/generic player compatibility
- **Pipe Bridge**: Native pipe output for Music Assistant integration
- **Playback Control**: Play/pause with silent frame injection

### 🎛️ Metadata & Control
- **Real-time Metadata**: Track title, artist, album, duration, position
- **Artwork Support**: Automatic artwork downloading and serving
- **Bidirectional Control**: WebSocket-based control commands
- **REST API**: Simple HTTP endpoints for external control

### 🔍 Discovery
- **UDP Discovery**: Auto-discovery via UDP broadcast on port 12888
- **Zero Configuration**: Automatic IP detection and server info broadcast

### 🌐 Optional Web Dashboard
- **Embedded Player**: Built-in responsive web player (use `--web` flag)
- **Real-time Updates**: Live metadata and playback position
- **Media Controls**: Play, pause, next, previous buttons

## 📦 Installation

### Pre-compiled Binaries (Recommended)

Download the binary for your platform from the Releases

```bash
# macOS (Intel)
./bin/ariacast_darwin_amd64 --pipe /tmp/music_assistant.pcm

# macOS (Apple Silicon)
./bin/ariacast_darwin_arm64 --pipe /tmp/music_assistant.pcm

# Linux (x64)
./bin/ariacast_linux_amd64 --pipe /tmp/music_assistant.pcm

# Linux (ARM - Raspberry Pi)
./bin/ariacast_linux_arm --pipe /tmp/music_assistant.pcm

# Linux (ARM64)
./bin/ariacast_linux_arm64 --pipe /tmp/music_assistant.pcm
```

### Build from Source

```bash
# Clone the repository
git clone https://github.com/AirPlr/Ariacast-Server-GO.git
cd Ariacast-Server-GO

# Build
./build.sh

# Run
./ariacast_OS_ARCH --web
```

## 🎯 Usage

### Basic Usage (Pipe Mode)
For Music Assistant integration:
```bash
./ariacast_OS_ARCH --pipe /tmp/music_assistant.pcm
```

The server will:
- Create a pipe bridge to write audio data
- Start discovery service on UDP port 12888
- Listen for WebSocket connections on port 12889
- Write silence when playback is paused


### Standalone (No Pipe)
```bash
./ariacast_OS_ARCH --web
```
Access the web player at: `http://localhost:8080`

## 📡 API Reference

### WebSocket Endpoints (Port 12889)

#### `/audio` - Audio Stream
- **Type**: Binary WebSocket
- **Frame Size**: 3840 bytes (20ms of PCM audio)
- **Format**: 48kHz, 16-bit, Stereo
- **Flow**: Client → Server

**Handshake Response:**
```json
{
  "status": "READY",
  "sample_rate": 48000,
  "channels": 2,
  "frame_size": 3840
}
```

#### `/metadata` - Metadata Updates
- **Type**: JSON WebSocket
- **Flow**: Bidirectional

**Update Metadata (Client → Server):**
```json
{
  "type": "update",
  "data": {
    "title": "Song Title",
    "artist": "Artist Name",
    "album": "Album Name",
    "artworkUrl": "https://example.com/cover.jpg",
    "durationMs": 240000,
    "positionMs": 45000,
    "isPlaying": true
  }
}
```

**Broadcast to Clients (Server → Clients):**
```json
{
  "type": "metadata",
  "data": {
    "title": "Song Title",
    "artist": "Artist Name",
    "album": "Album Name",
    "artwork_url": "https://example.com/cover.jpg",
    "duration_ms": 240000,
    "position_ms": 45000,
    "is_playing": true
  }
}
```

#### `/control` - Playback Control
- **Type**: JSON WebSocket
- **Flow**: Bidirectional

**Control Commands:**
```json
{"action": "play"}
{"action": "pause"}
{"action": "next"}
{"action": "previous"}
```

### HTTP Endpoints

#### `GET /stream.wav` (Port 8080, requires `--web`)
Infinite WAV stream for browser playback.

#### `GET /artwork` (Port 12889)
Serves the current track's artwork (JPEG).

#### `POST /api/command` (Port 12889)
Send control commands via HTTP:
```bash
curl -X POST http://localhost:12889/api/command \
  -H "Content-Type: application/json" \
  -d '{"action": "pause"}'
```

#### `GET /image/artwork` (Port 12889)
Alternative artwork endpoint for Music Assistant.

### UDP Discovery (Port 12888)

**Discovery Request:**
```
DISCOVER_AUDIOCAST
```

**Response:**
```json
{
  "server_name": "MusicAssistant AriaCast Receiver",
  "ip": "192.168.1.100",
  "port": 12889,
  "samplerate": 48000,
  "channels": 2
}
```

## 🏗️ Architecture

```
┌─────────────────────────────────────┐
│      AriaCast Client/Source         │
│  (Music Assistant, Custom Client)   │
└──────────────┬──────────────────────┘
               │
               │ UDP Discovery (12888)
               │ WebSocket (12889)
               │
┌──────────────▼──────────────────────┐
│       AriaCast Go Server            │
│  ┌──────────────────────────────┐  │
│  │ Discovery Service (UDP)      │  │
│  ├──────────────────────────────┤  │
│  │ Audio Handler (/audio)       │  │
│  ├──────────────────────────────┤  │
│  │ Metadata Handler (/metadata) │  │
│  ├──────────────────────────────┤  │
│  │ Control Handler (/control)   │  │
│  ├──────────────────────────────┤  │
│  │ Pipe Bridge → Named Pipe     │  │
│  ├──────────────────────────────┤  │
│  │ Web Dashboard (optional)     │  │
│  └──────────────────────────────┘  │
└──────────────┬──────────────────────┘
               │
               │ Named Pipe (PCM)
               │
┌──────────────▼──────────────────────┐
│      Music Assistant Player         │
│         (or other consumer)         │
└─────────────────────────────────────┘
```

## 🔄 Differences from Python Version

| Feature | Python Version | Go Version |
|---------|----------------|------------|
| **Local Audio Playback** | ✅ (sounddevice) | ❌ (pipe only) |
| **Pipe Output** | ⚠️ (limited) | ✅ (native, optimized) |
| **Web Dashboard** | ✅ (rich, visualizer) | ✅ (minimal) |
| **mDNS Discovery** | ✅ (Bonjour) | ❌ (UDP only) |
| **Dependencies** | Many (aiohttp, numpy, etc.) | 1 (gorilla/websocket) |
| **CPU Usage** | ~5-10% | ~1-2% |
| **Memory** | ~50-80 MB | ~10-15 MB |
| **Deployment** | Requires Python + packages | Single binary |
| **Pause Control** | Limited | ✅ (silence injection) |

**Choose Python version if you need:**
- Local audio playback via sounddevice
- Rich web interface with visualizer
- mDNS/Bonjour discovery

**Choose Go version if you need:**
- Music Assistant pipe integration
- Low resource usage
- Single binary deployment
- Better performance on embedded devices (Raspberry Pi)

## 🔧 Configuration

The server is configured with sensible defaults. To modify configuration, edit the `ServerConfig` struct in `main.go`:

```go
Config: &ServerConfig{
    ServerName:    "MusicAssistant AriaCast Receiver",
    StreamingPort: 12889,
    DiscoveryPort: 12888,
    Audio: AudioConfig{
        SampleRate:      48000,
        Channels:        2,
        SampleWidth:     2,
        FrameDurationMs: 20,
    },
}
```

## 🐳 Docker (Coming Soon)

```dockerfile
FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY . .
RUN go build -o ariacast main.go

FROM alpine:latest
COPY --from=builder /app/ariacast /usr/local/bin/
ENTRYPOINT ["ariacast"]
CMD ["--pipe", "/tmp/music_assistant.pcm", "--web"]
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit pull requests, report issues, or suggest enhancements.

### Development

```bash
# Install dependencies
go mod download

# Run with hot reload (requires air)
air

# Build for all platforms
./build.sh

# Run tests
go test ./...
```

## 📝 License

MIT License - see LICENSE file for details

## 🙏 Credits

- Original Python implementation: [AriaCast Server](https://github.com/AirPlr/Ariacast-server-python)
- WebSocket library: [gorilla/websocket](https://github.com/gorilla/websocket)

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/AirPlr/Ariacast-Server-GO/issues)
- **Discussions**: [GitHub Discussions](https://github.com/AirPlr/Ariacast-Server-GO/discussions)
- **Python Version**: [AriaCast Server Issues](https://github.com/AirPlr/Ariacast-server-python/issues)

---

**Made with ❤️ for Music Assistant and the audio streaming community**
