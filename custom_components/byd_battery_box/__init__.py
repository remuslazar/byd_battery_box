"""The BYD Battery Box integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from . import hub
from .const import CONF_BMS_SCAN_INTERVAL, CONF_LOG_SCAN_INTERVAL, CONF_UNIT_ID, DOMAIN
from .extmodbusclient import ModbusConnectionError

_LOGGER = logging.getLogger(__name__)

# List of platforms to support. There should be a matching .py file for each,
# eg <cover.py> and <sensor.py>
PLATFORMS = [Platform.SENSOR, Platform.BUTTON]

type HubConfigEntry = ConfigEntry[hub.Hub]

async def async_setup_entry(hass: HomeAssistant, entry: HubConfigEntry) -> bool:
    """Set up BYD Battery Box from a config entry."""

    name = entry.data[CONF_NAME]
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    unit_id = entry.data.get(CONF_UNIT_ID, 1)
    # Scan intervals set in the options dialog override those from the setup.
    settings = {**entry.data, **entry.options}
    scan_interval = settings[CONF_SCAN_INTERVAL]
    scan_interval_bms = settings[CONF_BMS_SCAN_INTERVAL]
    scan_interval_log = settings[CONF_LOG_SCAN_INTERVAL]

    _LOGGER.debug("Setup %s.%s", DOMAIN, name)

    # Store an instance of the "connecting" class that does the work of speaking
    # with your actual devices.
    entry.runtime_data = hub.Hub(hass = hass, name = name, host = host, port = port, unit_id=unit_id, scan_interval = scan_interval, scan_interval_bms = scan_interval_bms, scan_interval_log=scan_interval_log)

    try:
        await entry.runtime_data.init_data()
    except ModbusConnectionError as err:
        # The battery may still be off or starting, e.g. after a power cut:
        # Home Assistant retries the setup with a growing delay.
        await entry.runtime_data.close()
        raise ConfigEntryNotReady(str(err)) from err
    except Exception:
        await entry.runtime_data.close()
        raise

    # This creates each HA object for each platform your device requires.
    # It's done by calling the `async_setup_entry` function in each platform module.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This is called when an entry/configured device is to be removed. The class
    # needs to unload itself, and remove callbacks. See the classes for further
    # details
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Release the Modbus connection: the battery serves one client, so a
        # reload or a re-added entry cannot connect while it is still open.
        await entry.runtime_data.close()

    return unload_ok


