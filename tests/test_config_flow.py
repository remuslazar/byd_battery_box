"""Config and options flow against a fake one-client BMU."""

import asyncio

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.byd_battery_box.const import (
    CONF_BMS_SCAN_INTERVAL,
    CONF_LOG_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DOMAIN,
)

from .fake_bmu import FakeBmu


def user_input(port: int) -> dict:
    return {
        CONF_NAME: "BYD Battery Box",
        CONF_HOST: "127.0.0.1",
        CONF_PORT: port,
        CONF_UNIT_ID: 1,
        CONF_SCAN_INTERVAL: 60,
        CONF_BMS_SCAN_INTERVAL: 120,
        CONF_LOG_SCAN_INTERVAL: 1800,
    }


async def settle(hass: HomeAssistant) -> None:
    """Let the health monitor's first read and socket closes happen."""
    for _ in range(5):
        await asyncio.sleep(0.2)
        await hass.async_block_till_done()


async def add_entry(hass: HomeAssistant, port: int):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input(port))


async def test_setup_after_flow_gets_the_connection(hass: HomeAssistant, bmu: FakeBmu) -> None:
    """The flow's connection test must not keep the BMU's only connection."""
    result = await add_entry(hass, bmu.port)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await settle(hass)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    assert bmu.refused == 0
    assert bmu.open_connections == 1  # the entry's own client
    assert hass.states.get("sensor.battery_management_unit_towers").state == "1"
    assert hass.states.get("sensor.battery_management_unit_modules").state == "4"
    assert hass.states.get("sensor.battery_management_unit_cells_per_module").state == "32"
    assert hass.states.get("sensor.battery_management_unit_temperature_sensors_per_module").state == "12"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert bmu.open_connections == 0


async def test_failed_flow_leaves_no_connection(hass: HomeAssistant, bmu: FakeBmu) -> None:
    """Another client holds the BMU: cannot_connect, and nothing stays open."""
    _, holder = await asyncio.open_connection("127.0.0.1", bmu.port)
    await asyncio.sleep(0.1)

    result = await add_entry(hass, bmu.port)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    holder.close()
    await settle(hass)
    assert bmu.open_connections == 0


async def test_options_change_scan_intervals(hass: HomeAssistant, bmu: FakeBmu) -> None:
    await add_entry(hass, bmu.port)
    await settle(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 5, CONF_BMS_SCAN_INTERVAL: 300, CONF_LOG_SCAN_INTERVAL: 3600}
    )
    assert result["errors"] == {"base": "scan_interval_too_short"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_BMS_SCAN_INTERVAL: 300, CONF_LOG_SCAN_INTERVAL: 3600}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await settle(hass)

    # The reload closed the old client before the new one connected.
    assert entry.state is ConfigEntryState.LOADED
    assert bmu.refused == 0
    assert bmu.open_connections == 1
    hub = entry.runtime_data
    assert hub._scan_interval.total_seconds() == 30
    assert hub._scan_interval_bms.total_seconds() == 300
    assert hub._scan_interval_log.total_seconds() == 3600

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert bmu.open_connections == 0
