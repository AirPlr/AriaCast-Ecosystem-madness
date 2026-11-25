"""Adding a receiver: by address, and as discovered by mDNS."""

from ipaddress import ip_address
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ariacast.const import DOMAIN

from .fake_receiver import FakeReceiver

NO_SETUP = patch("custom_components.ariacast.async_setup_entry", return_value=True)


def discovery(host: str = "192.168.1.20", device_id: str | None = "abc123") -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        hostname="pixel.local.",
        name="Pixel 8._audiocast._tcp.local.",
        port=12889,
        properties={"device_id": device_id, "platform": "android"} if device_id else {},
        type="_audiocast._tcp.local.",
    )


async def test_user_flow_creates_an_entry(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with NO_SETUP:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: " 127.0.0.1 ", CONF_PORT: receiver.port}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: receiver.port}


async def test_user_flow_reports_an_unreachable_receiver_and_recovers(
    hass: HomeAssistant, receiver: FakeReceiver
) -> None:
    dead = FakeReceiver()
    await dead.start()
    dead_port = dead.port
    await dead.stop()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: dead_port}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    with NO_SETUP:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: receiver.port}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_refuses_the_same_address_twice(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id=f"127.0.0.1:{receiver.port}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: receiver.port},
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: receiver.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_asks_then_creates_an_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=discovery()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {"name": "Pixel 8"}
    with NO_SETUP:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pixel 8"
    assert result["data"] == {CONF_HOST: "192.168.1.20", CONF_PORT: 12889}
    assert result["result"].unique_id == "abc123"


async def test_zeroconf_without_device_id_uses_the_service_name(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=discovery(device_id=None)
    )
    with NO_SETUP:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["result"].unique_id == "Pixel 8"


async def test_zeroconf_follows_a_receiver_that_changed_address(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="abc123", data={CONF_HOST: "192.168.1.20", CONF_PORT: 12889}
    )
    entry.add_to_hass(hass)
    with NO_SETUP:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=discovery("192.168.1.99")
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.99"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
