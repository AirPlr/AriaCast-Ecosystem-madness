# AriaCast Receiver for Android
![GitHub Downloads (all assets, all releases)](https://img.shields.io/github/downloads/AriaCast/AriaCast-Receiver-for-Android/total?style=for-the-badge)

A high-performance Android port of AriaCast Server. This application transforms your Android device (tablet or phone) into a powerful audio streaming receiver, fully compatible with AriaCast-enabled clients.

## 🚀 Features

-   **High-Performance Audio**: Low-latency PCM audio playback using Android's native `AudioTrack` API.
-   **Dynamic Theming**: Automatically extracts color palettes from album artwork to generate smooth, animated gradient backgrounds.
-   **Smart Typography**: Intelligently adjusts text and UI element colors (black/white) based on the background luminance for perfect readability.
-   **Immersive UI**: A modern, XML-based horizontal layout designed specifically for the Amazon Echo Show 5.
-   **Full-Screen Mode**: Immersive experience with hidden system bars.
-   **Auto-Discovery**: Support for both mDNS (Bonjour) via JmDNS and UDP Broadcast discovery for "zero-configuration" setup.
-   **Real-Time Metadata**: Displays track title, artist, progress, and high-resolution artwork over WebSockets.

## 🛠 Tech Stack

-   **Server**: [Ktor](https://ktor.io/) (Netty engine) for handling WebSockets and HTTP API.
-   **Discovery**: [JmDNS](https://github.com/jmdns/jmdns) for mDNS service advertising.
-   **Image Loading**: [Coil](https://coil-kt.github.io/coil/) for efficient artwork fetching and crossfading.
-   **Color Extraction**: [Android Palette API](https://developer.android.com/develop/ui/views/graphics/palette) for dynamic UI styling.
-   **Language**: 100% Kotlin with Coroutines and StateFlow for reactive state management.

## 📡 Protocol Details

By default, the receiver operates on the following ports:
-   **Discovery Port**: `12888` (UDP)
-   **Streaming/Control/Metadata Port**: `12889` (TCP/WebSocket)

### Endpoints
-   `/audio`: Binary WebSocket for PCM stream input.
-   `/metadata`: JSON WebSocket for track information updates.
-   `/artwork`: HTTP endpoint serving the current cached album art.

## 📥 Installation

Download the latest version here: [Releases](https://github.com/AirPlr/AriaCast-Receiver-for-Android/releases/latest)
