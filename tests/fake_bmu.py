"""A fake BYD BMU: Modbus RTU framing over TCP, one client at a time.

The real BMU serves a single TCP connection. While one is open, a second
client is accepted and closed at once, which is what this fake does too.
It answers function 3 (read holding registers) from blocks keyed by their
start address, as the BMU does: the 20 registers read from 0x0000 and the 2
read from 0x0010 are separate answers, not one flat register space. Writes
(function 16) store their values as the block at that address.
"""

from __future__ import annotations

import asyncio
import struct


def crc16(frame: bytes) -> bytes:
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def string_registers(text: str, count: int) -> list[int]:
    raw = text.encode().ljust(count * 2, b"\0")
    return [raw[i] << 8 | raw[i + 1] for i in range(0, count * 2, 2)]


def hvs_registers(towers: int = 1, modules: int = 4) -> dict[int, list[int]]:
    """Info blocks of an HVS: serial, versions, towers/modules, type 2."""
    info = string_registers("P030T020Z0000000000", 10) + [
        0, 0,
        3 << 8 | 26,  # BMU A 3.26
        3 << 8 | 24,  # BMU B 3.24
        3 << 8 | 31,  # BMS 3.31
        0,  # working areas
        towers << 4 | modules,
        1 << 8,  # on grid
        1 << 8,  # three phase
        0,
    ]
    ext_info = [
        0 << 8,  # inverter Fronius HV
        2 << 8,  # battery type HVS
    ]
    return {0x0000: info, 0x0010: ext_info}


class FakeBmu:
    def __init__(self, blocks: dict[int, list[int]]) -> None:
        self.blocks = blocks
        self.open_connections = 0
        self.refused = 0
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> FakeBmu:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def stop(self) -> None:
        for writer in list(self._writers):
            writer.close()
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.open_connections:
            self.refused += 1
            writer.close()
            return
        self.open_connections += 1
        self._writers.add(writer)
        try:
            while True:
                head = await reader.readexactly(2)
                unit, function = head
                if function == 3:
                    body = await reader.readexactly(6)
                    address, count = struct.unpack(">HH", body[:4])
                    block = self.blocks.get(address, [])
                    values = [block[i] if i < len(block) else 0 for i in range(count)]
                    reply = bytes([unit, 3, count * 2]) + struct.pack(f">{count}H", *values)
                elif function == 16:
                    body = await reader.readexactly(5)
                    address, count, length = struct.unpack(">HHB", body)
                    data = await reader.readexactly(length + 2)
                    self.blocks[address] = list(struct.unpack(f">{count}H", data[:count * 2]))
                    reply = bytes([unit, 16]) + struct.pack(">HH", address, count)
                else:
                    return
                writer.write(reply + crc16(reply))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            self.open_connections -= 1
            writer.close()
