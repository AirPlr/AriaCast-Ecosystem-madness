"""The media player entity, end to end against the fake receiver."""

import asyncio

import pytest
from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PORT,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ariacast.const import DOMAIN

from .fake_receiver import JPEG, FakeReceiver

ENTITY = "media_player.kitchen"


async def setup_entry(hass: HomeAssistant, receiver: FakeReceiver) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", unique_id="kitchen-id",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: receiver.port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def wait_state(hass: HomeAssistant, predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate(hass.states.get(ENTITY)):
            await asyncio.sleep(0.01)


async def test_idle_receiver_shows_as_idle(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    await setup_entry(hass, receiver)
    await wait_state(hass, lambda s: s and s.state == STATE_IDLE)


async def test_playing_track_is_reflected(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    receiver.metadata = {
        "title": "Song", "artist": "Band", "album": "Record",
        "duration_ms": 200_000, "position_ms": 30_000, "is_playing": True,
    }
    await setup_entry(hass, receiver)
    await wait_state(hass, lambda s: s and s.state == STATE_PLAYING)
    state = hass.states.get(ENTITY)
    assert state.attributes["media_title"] == "Song"
    assert state.attributes["media_artist"] == "Band"
    assert state.attributes["media_album_name"] == "Record"
    assert state.attributes["media_duration"] == 200
    assert state.attributes["media_position"] == 30
    assert state.attributes["media_position_updated_at"] is not None


async def test_loaded_but_not_playing_is_paused(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    receiver.metadata = {"title": "Song", "is_playing": False}
    await setup_entry(hass, receiver)
    await wait_state(hass, lambda s: s and s.state == STATE_PAUSED)


async def test_live_updates_change_state(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    await setup_entry(hass, receiver)
    await wait_state(hass, lambda s: s and s.state == STATE_IDLE)
    await receiver.push(title="Song", isPlaying=True)
    await wait_state(hass, lambda s: s and s.state == STATE_PLAYING)
    await receiver.push(isPlaying=False)
    await wait_state(hass, lambda s: s and s.state == STATE_PAUSED)


@pytest.mark.parametrize(
    ("service", "command"),
    [
        ("media_play", "play"),
        ("media_pause", "pause"),
        ("media_stop", "stop"),
        ("media_next_track", "next"),
        ("media_previous_track", "previous"),
    ],
)
async def test_services_send_commands(
    hass: HomeAssistant, receiver: FakeReceiver, service: str, command: str
) -> None:
    await setup_entry(hass, receiver)
    await wait_state(hass, lambda s: s and s.state != STATE_UNAVAILABLE)
    await hass.services.async_call(MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY}, blocking=True)
    assert receiver.commands == [command]


async def test_a_failed_command_is_a_clean_error(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    await setup_entry(hass, receiver)
    await wait_state(hass, lambda s: s and s.state != STATE_UNAVAILABLE)
    receiver.command_status = 500
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(MP_DOMAIN, "media_play", {ATTR_ENTITY_ID: ENTITY}, blocking=True)


async def test_unreachable_receiver_is_unavailable_not_an_error(hass: HomeAssistant) -> None:
    dead = FakeReceiver()
    await dead.start()
    port = dead.port
    await dead.stop()
    entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", unique_id="kitchen-id",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
    await hass.config_entries.async_unload(entry.entry_id)


async def test_goes_unavailable_when_the_receiver_drops(
    hass: HomeAssistant, receiver: FakeReceiver, monkeypatch
) -> None:
    from custom_components.ariacast import client as client_module

    monkeypatch.setattr(client_module, "BACKOFF_START", 0.01)
    await setup_entry(hass, receiver)
    await wait_state(hass, lambda s: s and s.state == STATE_IDLE)
    await receiver.drop_metadata_connections()
    await wait_state(hass, lambda s: s and s.state == STATE_UNAVAILABLE)
    await wait_state(hass, lambda s: s and s.state == STATE_IDLE)


async def test_unload_stops_the_client_and_removes_the_entity(
    hass: HomeAssistant, receiver: FakeReceiver
) -> None:
    entry = await setup_entry(hass, receiver)
    client = entry.runtime_data
    await wait_state(hass, lambda s: s and s.state == STATE_IDLE)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert client._task is None
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE


async def test_artwork_is_proxied(hass: HomeAssistant, receiver: FakeReceiver, hass_client) -> None:
    receiver.metadata = {"title": "Song", "is_playing": True}
    await setup_entry(hass, receiver)
    await wait_state(hass, lambda s: s and s.state == STATE_PLAYING)
    state = hass.states.get(ENTITY)
    http = await hass_client()
    response = await http.get(state.attributes["entity_picture"])
    assert response.status == 200
    assert await response.read() == JPEG
