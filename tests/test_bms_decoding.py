"""Decoding of the BMS status packets (0x0558) for HVS and HVM towers."""

from unittest.mock import AsyncMock

import pytest

from custom_components.byd_battery_box.bydboxclient import BydBoxClient

# The 4 packets of 65 registers each start with a length register; the
# stream positions below are those of the registers after these are removed.
VOLTAGE_REGS = list(range(49, 65)) + list(range(66, 130)) + list(range(131, 180))
TEMP_REGS = list(range(180, 195)) + list(range(196, 213))


def bms_packet(cells_total: int, temps_total: int, balancing: set[int]) -> list[int]:
    """Cell k reads 3000 + k mV, temperature k reads k °C."""
    regs = [0] * 260
    for start in (0, 65, 130, 195):
        regs[start] = 128
    regs[1], regs[2] = 3200, 3000  # max/min cell voltage
    regs[15] = 1000  # charged energy, so the efficiency has a divisor
    for k in range(cells_total):
        regs[VOLTAGE_REGS[k]] = 3000 + k
    for k in range(0, temps_total, 2):
        regs[TEMP_REGS[k // 2]] = k << 8 | (k + 1)
    for cell in balancing:
        regs[7 + cell // 16] |= 1 << (cell % 16)
    return regs


def client_for(model: str, modules: int, cells: int, temps: int, regs: list[int]) -> BydBoxClient:
    client = BydBoxClient(host="127.0.0.1", port=8080, unit_id=1, timeout=3)
    client.data = {}
    client._modules, client._cells, client._temps = modules, cells, temps
    client.write_registers = AsyncMock()
    client._wait_for_response = AsyncMock(return_value=True)
    packets = [regs[i:i + 65] for i in range(0, 260, 65)]
    client.get_registers = AsyncMock(side_effect=packets)
    return client


@pytest.mark.parametrize(
    ("model", "modules", "cells", "temps"),
    [("HVS", 4, 32, 12), ("HVM", 5, 16, 8), ("HVM", 8, 16, 8)],
)
async def test_cells_follow_module_after_module(model, modules, cells, temps) -> None:
    balancing = {0, cells + 3, modules * cells - 1}
    client = client_for(model, modules, cells, temps, bms_packet(modules * cells, modules * temps, balancing))

    assert await client.update_bms_status_data(1)

    voltages = client.data["bms1_cell_voltages"]
    assert [m["v"] for m in voltages] == [[3000 + m * cells + c for c in range(cells)] for m in range(modules)]

    temperatures = client.data["bms1_cell_temps"]
    assert [m["t"] for m in temperatures] == [[m * temps + t for t in range(temps)] for m in range(modules)]

    flags = client.data["bms1_cell_balancing"]
    assert [len(m["b"]) for m in flags] == [max(16, cells)] * modules
    flat = [b for m in flags for b in m["b"]]
    assert {i for i, b in enumerate(flat) if b} == balancing
    assert client.data["bms1_balancing_qty"] == len(balancing)

    # min/max/average see every cell, not only the first modules'
    assert client.data["bms1_max_c_v"] == round((3000 + modules * cells - 1) / 1000, 3)
    assert client.data["bms1_min_c_v"] == 3.0
