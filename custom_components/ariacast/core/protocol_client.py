"""Low-level client for the real AriaCast wire protocol.

Speaks exactly what `Ariacast-server-python` (aiohttp) and `AriaCast-Server-GO`
(gorilla/websocket) implement — see AriaCast-Protocol-Spec/spec/*.md:

- UDP broadcast discovery on port 12888 ("DISCOVER_AUDIOCAST")
- mDNS discovery of `_audiocast._tcp.local.`
- WebSocket `/control`  (bidirectional JSON commands)
- WebSocket `/metadata` (bidirectional JSON now-playing state)
- WebSocket `/stats`    (receiver -> sender buffer stats, Python server only)
- HTTP POST `/api/command` (stateless command alternative)

This module intentionally has zero Home Assistant imports so it can run
standalone inside the `ariacast_core` add-on as well as inside the HACS
integration.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

DISCOVERY_PORT = 12888
DEFAULT_STREAM_PORT = 12889
DISCOVERY_MAGIC = "DISCOVER_AUDIOCAST"
MDNS_SERVICE_TYPE = "_audiocast._tcp.local."

# Both servers happily coexist with normal RFC6455 ping/pong frames even
# though the JSON control protocol itself defines no heartbeat. Using the
# transport-level ping avoids inventing a protocol extension that older
# receivers would have to understand.
HEARTBEAT_INTERVAL_S = 1.5
HEARTBEAT_TIMEOUT_S = 4.0


def _normalize_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """Fold camelCase (Android) and snake_case (server) keys into one shape."""
    aliases = {
        "artworkUrl": "artwork_url",
        "durationMs": "duration_ms",
        "positionMs": "position_ms",
        "isPlaying": "is_playing",
    }
    out = dict(data)
    for camel, snake in aliases.items():
        if camel in out and snake not in out:
            out[snake] = out[camel]
    return out


@dataclass
class DiscoveredSpeaker:
    server_name: str
    ip: str
    port: int
    samplerate: int = 48000
    channels: int = 2
    platform: str = ""
    version: str = ""


async def discover_udp(timeout: float = 2.0, attempts: int = 1) -> list[DiscoveredSpeaker]:
    """Broadcast DISCOVER_AUDIOCAST and collect UDP responses."""
    loop = asyncio.get_event_loop()
    found: dict[str, DiscoveredSpeaker] = {}

    class _Proto(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data: bytes, addr):
            try:
                payload = json.loads(data.decode("utf-8"))
                ip = payload.get("ip") or addr[0]
                found[ip] = DiscoveredSpeaker(
                    server_name=payload.get("server_name", "AriaCast Speaker"),
                    ip=ip,
                    port=int(payload.get("port", DEFAULT_STREAM_PORT)),
                    samplerate=int(payload.get("samplerate", 48000)),
                    channels=int(payload.get("channels", 2)),
                )
            except Exception:  # noqa: BLE001 - discovery must never crash
                logger.debug("Ignoring malformed discovery response from %s", addr)

    transport, _proto = await loop.create_datagram_endpoint(
        _Proto,
        local_addr=("0.0.0.0", 0),
        allow_broadcast=True,
    )
    sock: socket.socket = transport.get_extra_info("socket")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    try:
        for _ in range(max(1, attempts)):
            transport.sendto(DISCOVERY_MAGIC.encode("utf-8"), ("255.255.255.255", DISCOVERY_PORT))
            await asyncio.sleep(timeout)
    finally:
        transport.close()

    return list(found.values())


async def discover_mdns(timeout: float = 3.0) -> list[DiscoveredSpeaker]:
    """Discover receivers advertised via zeroconf/_audiocast._tcp.local."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        logger.warning("zeroconf not installed; skipping mDNS discovery")
        return []

    found: dict[str, DiscoveredSpeaker] = {}
    zc = Zeroconf()

    class _Listener:
        def add_service(self, zeroconf, service_type, name):
            info = zeroconf.get_service_info(service_type, name)
            if not info or not info.addresses:
                return
            ip = socket.inet_ntoa(info.addresses[0])
            props = {k.decode(): v.decode() for k, v in (info.properties or {}).items() if v is not None}
            found[ip] = DiscoveredSpeaker(
                server_name=name.replace(f".{service_type}", ""),
                ip=ip,
                port=info.port or DEFAULT_STREAM_PORT,
                samplerate=int(props.get("samplerate", 48000)),
                channels=int(props.get("channels", 2)),
                platform=props.get("platform", ""),
                version=props.get("version", ""),
            )

        def remove_service(self, zeroconf, service_type, name):
            pass

        def update_service(self, zeroconf, service_type, name):
            self.add_service(zeroconf, service_type, name)

    listener = _Listener()
    ServiceBrowser(zc, MDNS_SERVICE_TYPE, listener)
    try:
        await asyncio.sleep(timeout)
    finally:
        zc.close()
    return list(found.values())


MetadataCallback = Callable[[dict[str, Any]], Awaitable[None]]
StatsCallback = Callable[[dict[str, Any]], Awaitable[None]]
ConnectionCallback = Callable[[bool], Awaitable[None]]


class AriaCastSpeakerClient:
    """Persistent connection to one AriaCast receiver (/control + /metadata + /stats)."""

    def __init__(self, host: str, port: int = DEFAULT_STREAM_PORT, *, session: Optional[aiohttp.ClientSession] = None):
        self.host = host
        self.port = port
        self._own_session = session is None
        self._session = session
        self._control_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._metadata_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._stats_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._audio_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._tasks: list[asyncio.Task] = []
        self._on_metadata: Optional[MetadataCallback] = None
        self._on_stats: Optional[StatsCallback] = None
        self._on_connection_change: Optional[ConnectionCallback] = None
        self._connected = False
        self._last_pong_monotonic = 0.0
        self._stopping = False
        self.current_volume: int = -1
        self.volume_available: bool = False
        self.is_music_platform: bool = False

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def ws_base(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        return self._connected

    def on_metadata(self, callback: MetadataCallback) -> None:
        self._on_metadata = callback

    def on_stats(self, callback: StatsCallback) -> None:
        self._on_stats = callback

    def on_connection_change(self, callback: ConnectionCallback) -> None:
        self._on_connection_change = callback

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def start(self) -> None:
        """Open persistent /control and /metadata connections and keep them alive."""
        self._stopping = False
        self._tasks.append(asyncio.create_task(self._control_loop()))
        self._tasks.append(asyncio.create_task(self._metadata_loop()))

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        for ws in (self._control_ws, self._metadata_ws, self._stats_ws, self._audio_ws):
            if ws and not ws.closed:
                await ws.close()
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()
        await self._set_connected(False)

    async def _set_connected(self, value: bool) -> None:
        if value == self._connected:
            return
        self._connected = value
        if self._on_connection_change:
            await self._on_connection_change(value)

    async def _control_loop(self) -> None:
        """Maintain the /control WebSocket; this connection IS the heartbeat channel."""
        backoff = 1.0
        while not self._stopping:
            try:
                session = await self._get_session()
                async with session.ws_connect(
                    f"{self.ws_base}/control",
                    heartbeat=HEARTBEAT_INTERVAL_S,
                    timeout=HEARTBEAT_TIMEOUT_S,
                ) as ws:
                    self._control_ws = ws
                    self._last_pong_monotonic = time.monotonic()
                    await self._set_connected(True)
                    backoff = 1.0
                    logger.info("AriaCast control channel up: %s:%s", self.host, self.port)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._last_pong_monotonic = time.monotonic()
                            try:
                                data = json.loads(msg.data)
                            except json.JSONDecodeError:
                                continue
                            if "current_volume" in data:
                                self.current_volume = int(data.get("current_volume", -1))
                                self.volume_available = bool(data.get("volume_available", False))
                            if "level" in data and data.get("command") in ("volume", "volume_set"):
                                self.current_volume = int(data["level"])
                        elif msg.type in (aiohttp.WSMsgType.PONG,):
                            self._last_pong_monotonic = time.monotonic()
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("Control channel error for %s:%s: %s", self.host, self.port, exc)
            finally:
                self._control_ws = None
                await self._set_connected(False)
            if self._stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 32.0)

    async def _metadata_loop(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                session = await self._get_session()
                async with session.ws_connect(f"{self.ws_base}/metadata", heartbeat=HEARTBEAT_INTERVAL_S) as ws:
                    self._metadata_ws = ws
                    backoff = 1.0
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        try:
                            payload = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        if payload.get("type") == "metadata" and self._on_metadata:
                            await self._on_metadata(_normalize_metadata(payload.get("data", {})))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("Metadata channel error for %s:%s: %s", self.host, self.port, exc)
            finally:
                self._metadata_ws = None
            if self._stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 32.0)

    def is_heartbeat_alive(self, now: Optional[float] = None) -> bool:
        if not self._connected:
            return False
        now = now if now is not None else time.monotonic()
        return (now - self._last_pong_monotonic) < HEARTBEAT_TIMEOUT_S

    # -- commands -----------------------------------------------------------

    async def _send_control(self, payload: dict[str, Any]) -> None:
        if self._control_ws and not self._control_ws.closed:
            await self._control_ws.send_json(payload)
            return
        # Fall back to the stateless HTTP endpoint if the WS isn't up yet.
        session = await self._get_session()
        try:
            async with session.post(f"{self.base_url}/api/command", json=payload, timeout=aiohttp.ClientTimeout(total=3)):
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("HTTP command fallback failed for %s:%s: %s", self.host, self.port, exc)

    async def play(self) -> None:
        await self._send_control({"command": "play", "action": "play"})

    async def pause(self) -> None:
        await self._send_control({"command": "pause", "action": "pause"})

    async def toggle(self) -> None:
        await self._send_control({"command": "play_pause"})

    async def next_track(self) -> None:
        await self._send_control({"command": "next", "action": "next"})

    async def previous_track(self) -> None:
        await self._send_control({"command": "previous", "action": "previous"})

    async def stop(self) -> None:
        await self._send_control({"command": "stop", "action": "stop"})

    async def seek(self, position_ms: int) -> None:
        await self._send_control({"command": "seek", "action": "seek", "position_ms": int(position_ms)})

    async def set_volume(self, level: int) -> None:
        level = max(0, min(100, int(level)))
        await self._send_control({"command": "volume_set", "level": level})
        self.current_volume = level

    # -- audio sender role (used by AriaCastSocketServer to fan audio out) --

    async def open_audio_sender(self) -> dict[str, Any]:
        """Connect as a Sender to this receiver's /audio endpoint.

        Returns the READY handshake (sample_rate/channels/frame_size) per
        transport.md. Raises on timeout/rejection (e.g. HTTP 403 if the
        receiver already has an active Sender).
        """
        session = await self._get_session()
        ws = await session.ws_connect(f"{self.ws_base}/audio", timeout=5)
        self._audio_ws = ws
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=3.0)
        except asyncio.TimeoutError:
            await ws.close()
            self._audio_ws = None
            raise
        if msg.type != aiohttp.WSMsgType.TEXT:
            await ws.close()
            self._audio_ws = None
            raise ConnectionError("Expected JSON handshake on /audio")
        handshake = json.loads(msg.data)
        if handshake.get("status") != "READY" and handshake.get("type") != "handshake":
            await ws.close()
            self._audio_ws = None
            raise ConnectionError(f"Unexpected /audio handshake: {handshake}")
        return handshake

    async def send_audio_frame(self, frame: bytes) -> None:
        if self._audio_ws is None or self._audio_ws.closed:
            raise ConnectionError("audio sender not connected")
        await self._audio_ws.send_bytes(frame)

    async def close_audio_sender(self) -> None:
        if self._audio_ws and not self._audio_ws.closed:
            await self._audio_ws.close()
        self._audio_ws = None

    async def push_metadata(self, metadata: dict[str, Any]) -> None:
        """Used by the ducking manager to restore/override now-playing state."""
        session = await self._get_session()
        try:
            async with session.post(f"{self.base_url}/metadata", json={"data": metadata}, timeout=aiohttp.ClientTimeout(total=3)):
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("Metadata push failed for %s:%s: %s", self.host, self.port, exc)
