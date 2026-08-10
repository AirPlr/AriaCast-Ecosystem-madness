"""HaActionRelay: connects to the `ariacast_core` add-on's pubsub WebSocket
as a client and executes any `ha_action` message it broadcasts via
`hass.services.async_call` — this is how the add-on calls Home Assistant
services (`light.turn_on` for album-art hue sync, `media_player.play_media`
/`media_stop` for HA-bridged speaker playback) without needing its own
SUPERVISOR_TOKEN/HA_TOKEN: this integration already runs inside HA Core
with full service-call access, so routing the request back through here
needs no separate credential at all.

Only relevant when the add-on has no HA token of its own configured (see
`main.py`'s `_call_ha_service`) — Supervisor-mode deployments that already
get a token for free keep using the faster direct REST path there, and
this relay simply sits idle (connected, but nothing ever routes through
it) in that case.
"""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

logger = logging.getLogger(__name__)

RECONNECT_DELAY_S = 5.0


class HaActionRelay:
    def __init__(self, hass: HomeAssistant, addon_base_url: str):
        self.hass = hass
        self.addon_base_url = addon_base_url.rstrip("/")
        self._task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        self._stopped = False
        self._task = self.hass.async_create_task(self._run())

    def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()

    def _ws_url(self) -> str:
        parsed = urlparse(self.addon_base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/ws"

    async def _run(self) -> None:
        session = async_get_clientsession(self.hass)
        ws_url = self._ws_url()
        while not self._stopped:
            try:
                async with session.ws_connect(ws_url, heartbeat=15) as ws:
                    logger.info("ariacast action relay connected to %s", ws_url)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_message(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("ariacast action relay disconnected from %s: %s", ws_url, exc)
            if self._stopped:
                break
            await asyncio.sleep(RECONNECT_DELAY_S)

    async def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if data.get("type") != "ha_action":
            return  # snapshot / db_event — not for us, the Web UI/app handle those

        domain, service, entity_id = data.get("domain"), data.get("service"), data.get("entity_id")
        if not domain or not service or not entity_id:
            logger.warning("Malformed ha_action from add-on: %s", data)
            return
        params = data.get("params") or {}
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id, **params}, blocking=False)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to execute relayed HA action %s.%s for %s", domain, service, entity_id)
