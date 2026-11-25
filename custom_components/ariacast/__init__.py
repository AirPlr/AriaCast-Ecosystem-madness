"""AriaCast: one media player per AriaCast receiver on the network."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import AriaCastClient

PLATFORMS = [Platform.MEDIA_PLAYER]

type AriaCastConfigEntry = ConfigEntry[AriaCastClient]


async def async_setup_entry(hass: HomeAssistant, entry: AriaCastConfigEntry) -> bool:
    """Start following the receiver.

    A receiver that is switched off (a phone, a tablet) is not an error: the entity
    simply shows as unavailable and the client keeps trying until it comes back.
    """
    client = AriaCastClient(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT]
    )
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await client.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AriaCastConfigEntry) -> bool:
    """Stop following the receiver and remove its entities."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
