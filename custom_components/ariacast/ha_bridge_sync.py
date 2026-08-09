"""Pushes Home Assistant's own areas / `media_player` / `light` entities out
to the `ariacast_core` add-on so the DSP can route audio to (and sync
ambient lighting on) hardware that isn't native AriaCast — a Sonos, a Cast
group, an Alexa device, any bulb already in HA — not just AriaCast
receivers.

Runs entirely inside Home Assistant Core, which is the only place that has
`hass.states`/the entity, device and area registries; the add-on has no way
to see this on its own. One-way push (HA -> add-on) over the add-on's REST
API, since the two no longer necessarily share a filesystem once the add-on
is deployed on a separate host (see the standalone HA_TOKEN/HA_API_BASE
support in the add-on's main.py).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN

_TRACKED_DOMAINS = ("media_player", "light")

logger = logging.getLogger(__name__)

SYNC_INTERVAL_S = 60
_UNAVAILABLE_STATES = {"unavailable", "unknown", "none"}


def _entity_area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(entry.device_id)
        if device:
            return device.area_id
    return None


def _is_own_entity(hass: HomeAssistant, entity_id: str) -> bool:
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == DOMAIN


class HaBridgeSync:
    """Periodically (and on relevant state changes) mirrors HA areas plus
    non-AriaCast `media_player`/`light` entities into the add-on's DB."""

    def __init__(self, hass: HomeAssistant, addon_base_url: str):
        self.hass = hass
        self.addon_base_url = addon_base_url.rstrip("/")
        self._room_ids_by_area: dict[str, str] = {}
        self._unsubs: list[callable] = []

    def start(self) -> None:
        self._unsubs.append(
            async_track_time_interval(self.hass, self._sync_tick, timedelta(seconds=SYNC_INTERVAL_S))
        )
        self._unsubs.append(self.hass.bus.async_listen("state_changed", self._on_state_change))
        self.hass.async_create_task(self.sync_now())

    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    async def _sync_tick(self, _now) -> None:
        await self.sync_now()

    async def _on_state_change(self, event: Event) -> None:
        """Re-sync on availability changes only — a playing media_player's
        position/volume attributes tick every second or so and would
        otherwise re-POST every area/entity to the add-on that often."""
        entity_id = event.data.get("entity_id", "")
        if entity_id.split(".", 1)[0] not in _TRACKED_DOMAINS:
            return
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        old = old_state.state if old_state else None
        new = new_state.state if new_state else None
        if old == new:
            return
        if _is_own_entity(self.hass, entity_id):
            return
        await self.sync_now()

    async def sync_now(self) -> None:
        try:
            await self._sync_areas()
            await self._sync_media_players()
            await self._sync_lights()
        except Exception:  # noqa: BLE001 - a sync hiccup must never take HA down
            logger.exception("ariacast HA bridge sync failed")

    async def _post(self, path: str, payload: dict) -> dict | list | None:
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(f"{self.addon_base_url}{path}", json=payload, timeout=10) as resp:
                if resp.status >= 300:
                    logger.warning("ariacast_core %s returned HTTP %s", path, resp.status)
                    return None
                return await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.debug("ariacast_core add-on unreachable at %s: %s", self.addon_base_url, exc)
            return None

    async def _sync_areas(self) -> None:
        registry = ar.async_get(self.hass)
        for area in registry.async_list_areas():
            room = await self._post(
                "/api/rooms",
                {"name": area.name, "ha_area_id": area.id},
            )
            if room and room.get("id"):
                self._room_ids_by_area[area.id] = room["id"]

    async def _sync_media_players(self) -> None:
        speakers = []
        for state in self.hass.states.async_all("media_player"):
            if _is_own_entity(self.hass, state.entity_id):
                continue
            speakers.append(self._speaker_payload(state))
        if speakers:
            await self._post("/api/speakers/sync-ha", {"speakers": speakers})

    def _speaker_payload(self, state: State) -> dict:
        area_id = _entity_area_id(self.hass, state.entity_id)
        return {
            "entity_id": state.entity_id,
            "name": state.attributes.get("friendly_name", state.entity_id),
            "room_id": self._room_ids_by_area.get(area_id) if area_id else None,
            "available": state.state not in _UNAVAILABLE_STATES,
        }

    async def _sync_lights(self) -> None:
        for state in self.hass.states.async_all("light"):
            area_id = _entity_area_id(self.hass, state.entity_id)
            room_id = self._room_ids_by_area.get(area_id) if area_id else None
            if not room_id:
                continue  # ambient sync needs a room to belong to; skip unassigned lights
            await self._post(
                "/api/lights",
                {"ha_entity_id": state.entity_id, "room_id": room_id},
            )
