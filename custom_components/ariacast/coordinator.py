"""Bridges SpeakerNodeManager/DatabaseService events onto Home Assistant's
dispatcher so media_player entities update instantly, without polling."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, SIGNAL_SPEAKER_UPDATED
from .core.database import DatabaseService
from .core.dsp import RoomSpatialAudioDSP
from .core.models import Speaker, SpeakerStatus
from .core.node_manager import SpeakerNodeManager

logger = logging.getLogger(__name__)


class AriaCastCoordinator:
    """Owns the shared core services for one config entry and wires their
    events to HA's dispatcher bus so entities can subscribe without polling."""

    def __init__(self, hass: HomeAssistant, db: DatabaseService, node_manager: SpeakerNodeManager, dsp: RoomSpatialAudioDSP):
        self.hass = hass
        self.db = db
        self.node_manager = node_manager
        self.dsp = dsp

    def setup_listeners(self) -> None:
        self.node_manager.on_transition(self._on_transition)
        self.node_manager.on_metadata(self._on_metadata)

    async def _on_transition(self, speaker: Speaker, old_status: SpeakerStatus, new_status: SpeakerStatus) -> None:
        logger.debug("Dispatching status change for %s: %s -> %s", speaker.id, old_status, new_status)
        async_dispatcher_send(self.hass, f"{SIGNAL_SPEAKER_UPDATED}_{speaker.id}", speaker)
        if speaker.room_id:
            # A node dropping out or rejoining changes who's in the panning mix.
            await self.dsp.recompute_room(speaker.room_id)

    async def _on_metadata(self, speaker: Speaker, metadata: dict) -> None:
        async_dispatcher_send(self.hass, f"{SIGNAL_SPEAKER_UPDATED}_{speaker.id}", speaker, metadata)
