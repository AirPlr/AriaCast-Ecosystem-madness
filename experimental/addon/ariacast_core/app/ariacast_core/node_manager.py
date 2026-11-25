"""SpeakerNodeManager: dynamic lifecycle for AriaCast nodes (PCs that sleep,
laptops that close their lid, speakers that get unplugged).

Responsibilities:
  * run continuous discovery (UDP broadcast + mDNS) to find new hardware
  * hold one `AriaCastSpeakerClient` per known speaker and use its persistent
    `/control` WebSocket as the heartbeat channel (1-2s cadence, matching the
    spec's discovery retry cadence)
  * flip `speakers.status` between online/offline/unavailable in the DB the
    instant a node disappears or reappears
  * notify subscribers (the DSP engine, HA entity state) on every transition
  * apply a soft 200ms crossfade window on reconnect so `RoomSpatialAudioDSP`
    doesn't slam the speaker back in at full gain
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

import aiohttp

from .database import DatabaseService
from .models import Speaker, SpeakerStatus
from .protocol_client import AriaCastSpeakerClient, DiscoveredSpeaker, discover_mdns, discover_udp

logger = logging.getLogger(__name__)

RECONNECT_CROSSFADE_MS = 200
OFFLINE_GRACE_S = 6.0  # allow a couple of missed heartbeats before declaring OFFLINE
DISCOVERY_INTERVAL_S = 30.0

TransitionCallback = Callable[[Speaker, SpeakerStatus, SpeakerStatus], Awaitable[None]]


class ManagedNode:
    __slots__ = ("speaker", "client", "last_status", "offline_since", "reconnected_at")

    def __init__(self, speaker: Speaker, client: AriaCastSpeakerClient):
        self.speaker = speaker
        self.client = client
        self.last_status = speaker.status
        self.offline_since: Optional[float] = None
        self.reconnected_at: Optional[float] = None

    def is_in_crossfade(self, now: float) -> bool:
        return self.reconnected_at is not None and (now - self.reconnected_at) * 1000 < RECONNECT_CROSSFADE_MS

    def crossfade_factor(self, now: float) -> float:
        """0.0 -> 1.0 ramp over RECONNECT_CROSSFADE_MS after a reconnect."""
        if self.reconnected_at is None:
            return 1.0
        elapsed_ms = (now - self.reconnected_at) * 1000
        if elapsed_ms >= RECONNECT_CROSSFADE_MS:
            return 1.0
        return max(0.0, min(1.0, elapsed_ms / RECONNECT_CROSSFADE_MS))


class SpeakerNodeManager:
    def __init__(self, db: DatabaseService, *, session: Optional[aiohttp.ClientSession] = None):
        self.db = db
        self._session = session
        self._nodes: dict[str, ManagedNode] = {}  # keyed by speaker.id
        self._transition_listeners: list[TransitionCallback] = []
        self._metadata_listeners: list[Callable[[Speaker, dict], Awaitable[None]]] = []
        self._last_metadata: dict[str, dict] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._discovery_task: Optional[asyncio.Task] = None
        self._stopping = False

    def on_transition(self, callback: TransitionCallback) -> None:
        self._transition_listeners.append(callback)

    def on_metadata(self, callback: Callable[[Speaker, dict], Awaitable[None]]) -> None:
        self._metadata_listeners.append(callback)

    def get_node(self, speaker_id: str) -> Optional[ManagedNode]:
        return self._nodes.get(speaker_id)

    def online_nodes(self) -> list[ManagedNode]:
        return [n for n in self._nodes.values() if n.speaker.status == SpeakerStatus.ONLINE]

    def get_last_metadata(self, speaker_id: str) -> dict:
        return self._last_metadata.get(speaker_id, {})

    async def start(self) -> None:
        self._stopping = False
        for speaker in await self.db.list_speakers():
            await self._adopt(speaker)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._discovery_task = asyncio.create_task(self._discovery_loop())

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._heartbeat_task, self._discovery_task):
            if task:
                task.cancel()
        for node in self._nodes.values():
            await node.client.stop()
        self._nodes.clear()

    async def _adopt(self, speaker: Speaker) -> ManagedNode:
        client = AriaCastSpeakerClient(speaker.ip_address, speaker.port, session=self._session)
        node = ManagedNode(speaker, client)
        self._nodes[speaker.id] = node

        async def _on_conn(is_up: bool) -> None:
            await self._handle_connection_change(node, is_up)

        async def _on_meta(data: dict) -> None:
            self._last_metadata[node.speaker.id] = data
            is_playing = data.get("is_playing")
            if is_playing is not None:
                await self.db.update_speaker_playback(node.speaker.id, is_playing=bool(is_playing))
            for listener in self._metadata_listeners:
                await listener(node.speaker, data)

        client.on_connection_change(_on_conn)
        client.on_metadata(_on_meta)
        await client.start()
        return node

    async def register_speaker(
        self,
        *,
        hardware_uuid: str,
        ip_address: str,
        port: int,
        name: str,
        room_id: Optional[str] = None,
    ) -> Speaker:
        existing = await self.db.get_speaker_by_uuid(hardware_uuid)
        speaker = existing or Speaker(
            id=self.db.new_id(),
            room_id=room_id,
            ha_entity_id=None,
            hardware_uuid=hardware_uuid,
            ip_address=ip_address,
            port=port,
            name=name,
            status=SpeakerStatus.OFFLINE,
        )
        speaker.ip_address = ip_address
        speaker.port = port
        speaker.name = name
        if room_id:
            speaker.room_id = room_id
        await self.db.upsert_speaker(speaker)
        if speaker.id not in self._nodes:
            await self._adopt(speaker)
        return speaker

    async def remove_speaker(self, speaker_id: str) -> None:
        node = self._nodes.pop(speaker_id, None)
        if node:
            await node.client.stop()
        await self.db.delete_speaker(speaker_id)

    async def _handle_connection_change(self, node: ManagedNode, is_up: bool) -> None:
        now = time.time()
        if is_up:
            node.offline_since = None
            node.reconnected_at = now
            await self._set_status(node, SpeakerStatus.ONLINE)
        else:
            node.offline_since = now
            # Don't immediately mark OFFLINE — grace period is enforced in the heartbeat loop
            # so a single dropped frame doesn't cause a DSP re-balance storm.

    async def _set_status(self, node: ManagedNode, new_status: SpeakerStatus) -> None:
        old_status = node.speaker.status
        if old_status == new_status:
            return
        node.speaker.status = new_status
        await self.db.set_speaker_status(node.speaker.id, new_status)
        for listener in self._transition_listeners:
            await listener(node.speaker, old_status, new_status)
        logger.info("Speaker %s (%s) transitioned %s -> %s", node.speaker.name, node.speaker.id, old_status.value, new_status.value)

    async def _heartbeat_loop(self) -> None:
        """Poll node liveness at 1-2s cadence and enforce the offline grace period."""
        while not self._stopping:
            now = time.monotonic()
            wall_now = time.time()
            for node in list(self._nodes.values()):
                alive = node.client.is_heartbeat_alive(now)
                if alive and node.speaker.status != SpeakerStatus.ONLINE:
                    await self._set_status(node, SpeakerStatus.ONLINE)
                elif not alive and node.speaker.status == SpeakerStatus.ONLINE:
                    if node.offline_since is None:
                        node.offline_since = wall_now
                    elif wall_now - node.offline_since >= OFFLINE_GRACE_S:
                        await self._set_status(node, SpeakerStatus.OFFLINE)
            await asyncio.sleep(1.5)

    async def _discovery_loop(self) -> None:
        """Periodically sweep the LAN so new/renamed hardware gets adopted automatically."""
        while not self._stopping:
            try:
                found: list[DiscoveredSpeaker] = []
                found += await discover_udp(timeout=1.5)
                found += await discover_mdns(timeout=1.5)
                for d in found:
                    hw_uuid = f"{d.ip}:{d.port}"
                    existing = await self.db.get_speaker_by_uuid(hw_uuid)
                    if existing is None:
                        logger.info("Discovered new AriaCast speaker %s (%s:%s)", d.server_name, d.ip, d.port)
                        await self.register_speaker(
                            hardware_uuid=hw_uuid, ip_address=d.ip, port=d.port, name=d.server_name,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Discovery sweep failed")
            await asyncio.sleep(DISCOVERY_INTERVAL_S)
