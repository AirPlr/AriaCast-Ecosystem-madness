"""AriaCast Direct: native Home Assistant integration for AriaCast speakers.

Registers every AriaCast receiver on the LAN as a native `media_player`
entity — no Music Assistant or other middleware required. See
docs/ARCHITECTURE.md in the repo root for the full system design.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DATA_DB,
    DATA_DSP,
    DATA_HUE_SYNC,
    DATA_NODE_MANAGER,
    DATA_PUBSUB,
    DEFAULT_DB_FILENAME,
    DOMAIN,
    PLATFORMS,
    SERVICE_SET_LISTENER_POSITION,
    SERVICE_SET_ROOM_LAYOUT,
    SERVICE_SYNC_HA_AREAS,
)
from .coordinator import AriaCastCoordinator
from .core.database import DatabaseService
from .core.dsp import RoomSpatialAudioDSP
from .core.hue_sync import AlbumArtHueSync
from .core.models import Room
from .core.node_manager import SpeakerNodeManager
from .core.pubsub import PubSubServer

logger = logging.getLogger(__name__)

SET_ROOM_LAYOUT_SCHEMA = vol.Schema(
    {
        vol.Required("room_id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("ha_area_id"): cv.string,
        vol.Optional("width_meters", default=4.0): vol.Coerce(float),
        vol.Optional("height_meters", default=4.0): vol.Coerce(float),
    }
)

SET_LISTENER_POSITION_SCHEMA = vol.Schema(
    {
        vol.Required("room_id"): cv.string,
        vol.Required("x"): vol.Coerce(float),
        vol.Required("y"): vol.Coerce(float),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    db_path = hass.config.path(DEFAULT_DB_FILENAME)
    db = DatabaseService(db_path)
    await db.start()

    session = async_get_clientsession(hass)
    node_manager = SpeakerNodeManager(db, session=session)
    dsp = RoomSpatialAudioDSP(db)

    async def _call_light_service(entity_id: str, params: dict) -> None:
        await hass.services.async_call("light", "turn_on", {"entity_id": entity_id, **params}, blocking=False)

    hue_sync = AlbumArtHueSync(db, _call_light_service)
    pubsub = PubSubServer(db)
    await pubsub.start()

    coordinator = AriaCastCoordinator(hass, db, node_manager, dsp)
    coordinator.setup_listeners()

    await node_manager.start()

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_DB: db,
        DATA_NODE_MANAGER: node_manager,
        DATA_DSP: dsp,
        DATA_HUE_SYNC: hue_sync,
        DATA_PUBSUB: pubsub,
        "coordinator": coordinator,
    }
    if entry.data.get("addon_base_url"):
        hass.data[DOMAIN]["addon_base_url"] = entry.data["addon_base_url"]

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass, db, dsp)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data[DATA_NODE_MANAGER].stop()
        await data[DATA_PUBSUB].stop()
        await data[DATA_DB].stop()
    return unload_ok


def _register_services(hass: HomeAssistant, db: DatabaseService, dsp: RoomSpatialAudioDSP) -> None:
    async def handle_set_room_layout(call: ServiceCall) -> None:
        room = Room(
            id=call.data["room_id"],
            name=call.data["name"],
            ha_area_id=call.data.get("ha_area_id"),
            width_meters=call.data.get("width_meters", 4.0),
            height_meters=call.data.get("height_meters", 4.0),
        )
        await db.upsert_room(room)

    async def handle_set_listener_position(call: ServiceCall) -> None:
        await dsp.on_listener_moved(call.data["room_id"], call.data["x"], call.data["y"])

    async def handle_sync_ha_areas(call: ServiceCall) -> None:
        from homeassistant.helpers import area_registry as ar

        registry = ar.async_get(hass)
        for area in registry.async_list_areas():
            existing_rooms = await db.list_rooms()
            match = next((r for r in existing_rooms if r.ha_area_id == area.id), None)
            room = match or Room(id=db.new_id(), name=area.name, ha_area_id=area.id)
            room.name = area.name
            room.ha_area_id = area.id
            await db.upsert_room(room)

    if not hass.services.has_service(DOMAIN, SERVICE_SET_ROOM_LAYOUT):
        hass.services.async_register(DOMAIN, SERVICE_SET_ROOM_LAYOUT, handle_set_room_layout, schema=SET_ROOM_LAYOUT_SCHEMA)
        hass.services.async_register(DOMAIN, SERVICE_SET_LISTENER_POSITION, handle_set_listener_position, schema=SET_LISTENER_POSITION_SCHEMA)
        hass.services.async_register(DOMAIN, SERVICE_SYNC_HA_AREAS, handle_sync_ha_areas)
