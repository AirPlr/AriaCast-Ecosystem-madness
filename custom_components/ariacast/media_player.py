"""AriaCastMediaPlayer: native `media_player` entity for every AriaCast speaker.

Talks directly to the receiver's `/control` and `/metadata` WebSockets via
`AriaCastSpeakerClient` (see core/protocol_client.py) — no Music Assistant,
no external bridge. State transitions (`unavailable`/`off`/`playing`/`paused`)
come straight from `SpeakerNodeManager`'s heartbeat, so a PC that sleeps
shows up as `unavailable` in Home Assistant within a couple of seconds.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_DB, DATA_HUE_SYNC, DATA_NODE_MANAGER, DOMAIN, SIGNAL_SPEAKER_UPDATED
from .core.models import Speaker, SpeakerStatus
from .core.node_manager import SpeakerNodeManager

logger = logging.getLogger(__name__)

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.PLAY_MEDIA
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    db = data[DATA_DB]
    node_manager: SpeakerNodeManager = data[DATA_NODE_MANAGER]
    hue_sync = data[DATA_HUE_SYNC]

    known_ids: set[str] = set()

    @callback
    def _add_new_speakers() -> None:
        new_entities = []
        for node in node_manager._nodes.values():  # internal but same module family
            if node.speaker.id not in known_ids:
                known_ids.add(node.speaker.id)
                new_entities.append(AriaCastMediaPlayer(node.speaker.id, node_manager, db, hue_sync))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_speakers()

    # New speakers discovered after platform setup (e.g. a laptop joining the LAN
    # for the first time) get their entity created reactively too.
    async def _on_transition(speaker: Speaker, *_args) -> None:
        if speaker.id not in known_ids:
            _add_new_speakers()

    node_manager.on_transition(_on_transition)


class AriaCastMediaPlayer(MediaPlayerEntity):
    _attr_should_poll = False
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = SUPPORTED_FEATURES
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, speaker_id: str, node_manager: SpeakerNodeManager, db, hue_sync):
        self._speaker_id = speaker_id
        self._node_manager = node_manager
        self._db = db
        self._hue_sync = hue_sync
        self._attr_unique_id = f"{DOMAIN}_{speaker_id}"
        self._metadata: dict[str, Any] = {}

    @property
    def _speaker(self) -> Speaker | None:
        node = self._node_manager.get_node(self._speaker_id)
        return node.speaker if node else None

    @property
    def device_info(self) -> DeviceInfo:
        speaker = self._speaker
        return DeviceInfo(
            identifiers={(DOMAIN, self._speaker_id)},
            name=speaker.name if speaker else "AriaCast Speaker",
            manufacturer="AriaCast",
            model=speaker.platform or "AriaCast Receiver",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"{SIGNAL_SPEAKER_UPDATED}_{self._speaker_id}", self._handle_update)
        )
        self._metadata = self._node_manager.get_last_metadata(self._speaker_id)

    @callback
    def _handle_update(self, speaker: Speaker, metadata: dict | None = None) -> None:
        if metadata is not None:
            self._metadata = metadata
            image_url = metadata.get("artwork_url")
            if image_url and image_url.startswith("/") and speaker:
                self._metadata = {**metadata, "artwork_url": f"http://{speaker.ip_address}:{speaker.port}{image_url}"}
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        speaker = self._speaker
        return speaker is not None and speaker.status != SpeakerStatus.UNAVAILABLE

    @property
    def state(self) -> MediaPlayerState:
        speaker = self._speaker
        if speaker is None or speaker.status == SpeakerStatus.OFFLINE:
            return MediaPlayerState.OFF
        if speaker.is_playing:
            return MediaPlayerState.PLAYING
        return MediaPlayerState.PAUSED

    @property
    def volume_level(self) -> float | None:
        speaker = self._speaker
        if not speaker or speaker.volume < 0:
            return None
        return speaker.volume / 100

    @property
    def media_title(self) -> str | None:
        return self._metadata.get("title")

    @property
    def media_artist(self) -> str | None:
        return self._metadata.get("artist")

    @property
    def media_album_name(self) -> str | None:
        return self._metadata.get("album")

    @property
    def media_image_url(self) -> str | None:
        return self._metadata.get("artwork_url")

    @property
    def media_duration(self) -> int | None:
        ms = self._metadata.get("duration_ms")
        return int(ms / 1000) if ms is not None else None

    @property
    def media_position(self) -> int | None:
        ms = self._metadata.get("position_ms")
        return int(ms / 1000) if ms is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        speaker = self._speaker
        if not speaker:
            return {}
        return {
            "room_id": speaker.room_id,
            "gain_db": round(speaker.gain_db, 2),
            "delay_ms": round(speaker.delay_ms, 1),
            "hardware_uuid": speaker.hardware_uuid,
        }

    def _client(self):
        node = self._node_manager.get_node(self._speaker_id)
        return node.client if node else None

    async def async_media_play(self) -> None:
        client = self._client()
        if client:
            await client.play()

    async def async_media_pause(self) -> None:
        client = self._client()
        if client:
            await client.pause()

    async def async_media_stop(self) -> None:
        client = self._client()
        if client:
            await client.stop()

    async def async_media_next_track(self) -> None:
        client = self._client()
        if client:
            await client.next_track()

    async def async_media_previous_track(self) -> None:
        client = self._client()
        if client:
            await client.previous_track()

    async def async_media_seek(self, position: float) -> None:
        client = self._client()
        if client:
            await client.seek(int(position * 1000))

    async def async_set_volume_level(self, volume: float) -> None:
        client = self._client()
        if client:
            level = int(volume * 100)
            await client.set_volume(level)
            await self._db.update_speaker_playback(self._speaker_id, volume=level)

    async def async_mute_volume(self, mute: bool) -> None:
        client = self._client()
        if not client:
            return
        if mute:
            self._pre_mute_volume = client.current_volume
            await client.set_volume(0)
        else:
            await client.set_volume(getattr(self, "_pre_mute_volume", 50) or 50)

    async def async_turn_on(self) -> None:
        await self.async_media_play()

    async def async_turn_off(self) -> None:
        await self.async_media_stop()

    async def async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs: Any) -> None:
        """Play an ad-hoc notification (TTS, doorbell chime, alarm) with automatic ducking.

        A receiver only accepts one `/audio` Sender at a time, so this cannot
        simply open a second stream. It hands the clip off to the
        `ariacast_core` add-on's mixing bus (`AudioDuckingMixer`), which
        ducks the phone's music stream, mixes in the notification PCM, and
        restores the original volume — see core/ducking.py.
        """
        speaker = self._speaker
        if not speaker:
            logger.warning("Cannot play media on %s: speaker unknown", self._speaker_id)
            return

        addon_base = self.hass.data[DOMAIN].get("addon_base_url")
        if not addon_base:
            logger.warning(
                "ariacast_core add-on not configured; falling back to direct control "
                "(no audio ducking available for %s)",
                self._speaker_id,
            )
            client = self._client()
            if client:
                await client.push_metadata({"title": "Notification", "is_playing": True})
            return

        session = async_get_clientsession(self.hass)
        await session.post(
            f"{addon_base}/api/notify",
            json={"speaker_id": self._speaker_id, "media_id": media_id, "media_type": str(media_type)},
        )
