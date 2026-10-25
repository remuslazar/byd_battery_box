"""The BMU is off when Home Assistant starts, or drops out later."""

import asyncio
from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.byd_battery_box.const import DOMAIN

from .fake_bmu import FakeBmu, hvs_registers
from .test_config_flow import settle, user_input


def bmu_status(soc: int) -> list[int]:
    """The 21 BMU status registers from 0x0500, lifetime energy non-zero."""
    status = [0] * 21
    status[0] = soc
    status[17] = 1000  # charged 100 kWh
    status[19] = 900  # discharged 90 kWh
    return status


def registers(soc: int) -> dict[int, list[int]]:
    return {**hvs_registers(), 0x0500: bmu_status(soc)}


async def free_port() -> int:
    """A port nothing listens on, which the fake can take later."""
    fake = await FakeBmu({}).start()
    port = fake.port
    await fake.stop()
    return port


async def add_entry(hass: HomeAssistant, port: int, scan_interval: int = 60) -> MockConfigEntry:
    # The client's timeout is the scan interval less a second.
    entry = MockConfigEntry(domain=DOMAIN, data={**user_input(port), CONF_SCAN_INTERVAL: scan_interval})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_retries_until_the_bmu_answers(hass: HomeAssistant, socket_enabled) -> None:
    """A BMU that is off at start-up leaves the entry retrying, not failed."""
    port = await free_port()
    entry = await add_entry(hass, port)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "Failed to connect" in entry.reason

    bmu = await FakeBmu(registers(soc=57)).start(port)
    try:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
        await settle(hass)
        assert entry.state is ConfigEntryState.LOADED
        assert bmu.refused == 0
        assert bmu.open_connections == 1
        assert hass.states.get("sensor.battery_management_unit_modules").state == "4"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await settle(hass)
        assert bmu.open_connections == 0
    finally:
        await bmu.stop()


async def test_updates_resume_when_the_bmu_returns(hass: HomeAssistant, socket_enabled) -> None:
    """After setup, a BMU outage only fails the polls until it is back.

    The polls go on during the outage, and the first one after it reconnects
    while pymodbus's own reconnect loop (on by default) would be due too:
    that loop opened a second connection beside the live one, the one-client
    BMU refused it, and no poll succeeded again until a reload.
    """
    bmu = await FakeBmu(registers(soc=57)).start()
    port = bmu.port
    entry = await add_entry(hass, port, scan_interval=10)
    await settle(hass)
    assert entry.state is ConfigEntryState.LOADED
    hub = entry.runtime_data
    # Poll the BMU status only; the BMS and log reads have no fake answers.
    hub._last_full_update = hub._last_log_update = dt_util.now().replace(tzinfo=None) + timedelta(days=1)

    async def poll() -> bool:
        hub._last_update -= timedelta(seconds=5)  # past the 1 s minimum
        return await hub.async_update_data()

    assert await poll() is True
    assert hub.data["soc"] == 57

    await bmu.stop()
    await asyncio.sleep(0.5)
    assert await poll() is False
    assert entry.state is ConfigEntryState.LOADED

    bmu = await FakeBmu(registers(soc=58)).start(port)
    try:
        assert await poll() is True
        assert hub.data["soc"] == 58
        await settle(hass)
        assert bmu.refused == 0
        assert await poll() is True
        assert bmu.open_connections == 1

        assert await hass.config_entries.async_unload(entry.entry_id)
        await settle(hass)
        assert bmu.open_connections == 0
    finally:
        await bmu.stop()
