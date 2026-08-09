"""Config flow for the AriaCast Direct integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_ADDON_URL, CONF_HA_MODE, DOMAIN


class AriaCastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Single-instance hub: one entry manages every discovered AriaCast speaker."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="AriaCast Direct", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HA_MODE, default=True): bool,
                vol.Optional(CONF_ADDON_URL, default="http://a0d7b954-ariacast-core:8099"): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "AriaCastOptionsFlow":
        return AriaCastOptionsFlow(config_entry)


class AriaCastOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(CONF_HA_MODE, True)
        schema = vol.Schema({vol.Required(CONF_HA_MODE, default=current): bool})
        return self.async_show_form(step_id="init", data_schema=schema)
