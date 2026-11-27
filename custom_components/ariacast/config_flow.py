"""Config flow: found by mDNS, or entered by hand."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import AriaCastClient, AriaCastError
from .const import DEFAULT_PORT, DOMAIN

SERVICE_SUFFIX = "._audiocast._tcp.local."


class AriaCastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a receiver."""

    VERSION = 1

    def __init__(self) -> None:
        """Remember what discovery found between the two zeroconf steps."""
        self._host = ""
        self._port = DEFAULT_PORT
        self._name = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a receiver by address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            client = AriaCastClient(async_get_clientsession(self.hass), host, port)
            try:
                await client.async_validate()
            except AriaCastError:
                errors["base"] = "cannot_connect"
            else:
                # Without mDNS there is no device id, so the address identifies it.
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=host, data={CONF_HOST: host, CONF_PORT: port}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                        int, vol.Range(min=1, max=65535)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A receiver announced itself on the network."""
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        self._name = discovery_info.name.removesuffix(SERVICE_SUFFIX)
        device_id = discovery_info.properties.get("device_id") or self._name
        await self.async_set_unique_id(device_id)
        # A phone that got a new IP address updates its entry instead of duplicating it.
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host, CONF_PORT: self._port}
        )
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before adding what was discovered."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._name, data={CONF_HOST: self._host, CONF_PORT: self._port}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf_confirm", description_placeholders={"name": self._name}
        )
