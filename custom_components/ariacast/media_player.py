"""The AriaCast media player."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AriaCastConfigEntry
from .client import AriaCastClient, AriaCastError
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AriaCastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the receiver's media player."""
    async_add_entities([AriaCastMediaPlayer(entry)])


class AriaCastMediaPlayer(MediaPlayerEntity):
    """What an AriaCast receiver is playing, with transport controls."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    def __init__(self, entry: AriaCastConfigEntry) -> None:
        """Bind to the entry's client."""
        self._client: AriaCastClient = entry.runtime_data
        self._attr_unique_id = entry.unique_id or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            manufacturer="AriaCast",
            name=entry.title,
        )

    async def async_added_to_hass(self) -> None:
        """Follow the client's updates."""
        self.async_on_remove(self._client.add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        """Whether the receiver is reachable."""
        return self._client.available

    @property
    def state(self) -> MediaPlayerState:
        """Playing, paused (a track is loaded) or idle."""
        meta = self._client.metadata
        if meta.is_playing:
            return MediaPlayerState.PLAYING
        if meta.title:
            return MediaPlayerState.PAUSED
        return MediaPlayerState.IDLE

    @property
    def media_title(self) -> str | None:
        """Title of the current track."""
        return self._client.metadata.title

    @property
    def media_artist(self) -> str | None:
        """Artist of the current track."""
        return self._client.metadata.artist

    @property
    def media_album_name(self) -> str | None:
        """Album of the current track."""
        return self._client.metadata.album

    @property
    def media_duration(self) -> int | None:
        """Length of the current track in seconds."""
        ms = self._client.metadata.duration_ms
        return ms // 1000 if ms > 0 else None

    @property
    def media_position(self) -> int | None:
        """Position when the receiver last reported it, in seconds."""
        meta = self._client.metadata
        return meta.position_ms // 1000 if meta.duration_ms > 0 else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When :attr:`media_position` was reported."""
        meta = self._client.metadata
        return meta.updated_at if meta.duration_ms > 0 else None

    @property
    def media_image_hash(self) -> str | None:
        """Changes with the track, so the frontend fetches new artwork."""
        meta = self._client.metadata
        if not (meta.title or meta.artwork_url):
            return None
        return str(hash((meta.title, meta.artist, meta.album, meta.artwork_url)))

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Artwork, proxied through Home Assistant (the receiver is http-only)."""
        artwork = await self._client.async_artwork()
        return artwork if artwork else (None, None)

    async def _command(self, command: str) -> None:
        try:
            await self._client.async_command(command)
        except AriaCastError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_media_play(self) -> None:
        """Resume playback."""
        await self._command("play")

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._command("pause")

    async def async_media_stop(self) -> None:
        """Stop playback."""
        await self._command("stop")

    async def async_media_next_track(self) -> None:
        """Skip to the next track."""
        await self._command("next")

    async def async_media_previous_track(self) -> None:
        """Go back to the previous track."""
        await self._command("previous")
