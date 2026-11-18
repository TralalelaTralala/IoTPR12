"""
Перевірка Modbus-емулятора напряму (без openHAB): усі коди функцій 01-06, 15, 16
для Unit 1 (Modbus TCP) і Unit 2 (Modbus RTU over TCP).

Запуск:  docker compose run --rm --entrypoint python modbus-sim modbus_client_test.py
"""
import asyncio
import os
import struct

from pymodbus.client import AsyncModbusTcpClient

try:
    from pymodbus.framer import Framer

    RTU = Framer.RTU
except ImportError:  # pragma: no cover
    from pymodbus.transaction import ModbusRtuFramer as RTU

HOST = os.getenv("MODBUS_HOST", "172.30.12.10")


def ok(r) -> bool:
    return not r.isError()


async def main() -> None:
    u1 = AsyncModbusTcpClient(HOST, port=5020)
    u2 = AsyncModbusTcpClient(HOST, port=5021, framer=RTU)
    await u1.connect()
    await u2.connect()
    print(f"connected: unit1 TCP={u1.connected}, unit2 RTU/TCP={u2.connected}")

    r = await u1.read_input_registers(0, 6, slave=1)                               # FC04
    t = struct.unpack(">h", struct.pack(">H", r.registers[0]))[0] / 10
    energy = struct.unpack(">f", struct.pack(">HH", r.registers[4], r.registers[5]))[0]
    print(f"FC04 unit1 IR0..5 = {r.registers}  -> T={t} °C, L={r.registers[1] / 10} %, P={r.registers[2] / 100} bar, E={energy:.4f} kWh")

    r = await u1.read_holding_registers(0, 3, slave=1)                             # FC03
    print(f"FC03 unit1 HR0..2 = {r.registers}  -> SP={r.registers[0] / 10} °C")
    print(f"FC06 unit1 HR0 := 620          ok={ok(await u1.write_register(0, 620, slave=1))}")
    print(f"FC16 unit1 HR1..2 := [880,200] ok={ok(await u1.write_registers(1, [880, 200], slave=1))}")
    print(f"FC03 readback     = {(await u1.read_holding_registers(0, 3, slave=1)).registers}")

    r = await u1.read_coils(0, 2, slave=1)                                         # FC01
    print(f"FC01 unit1 coils  = {r.bits[:2]}")
    print(f"FC05 unit1 coil0 := 1          ok={ok(await u1.write_coil(0, True, slave=1))}")
    print(f"FC15 unit1 coils := [1,1]      ok={ok(await u1.write_coils(0, [True, True], slave=1))}")
    r = await u1.read_discrete_inputs(0, 3, slave=1)                               # FC02
    print(f"FC02 unit1 DI0..2 = {r.bits[:3]}")

    r = await u2.read_input_registers(0, 6, slave=2)
    runtime = (r.registers[4] << 16) | r.registers[5]
    print(f"FC04 unit2 IR0..5 = {r.registers}  -> rpm={r.registers[0]}, I={r.registers[1] / 100} A, runtime={runtime} s (uint32)")
    print(f"FC06 unit2 HR0 := 80           ok={ok(await u2.write_register(0, 80, slave=2))}")
    print(f"FC05 unit2 coil0 := 1 (RUN)    ok={ok(await u2.write_coil(0, True, slave=2))}")
    await asyncio.sleep(3)
    r = await u2.read_input_registers(0, 3, slave=2)
    print(f"after 3 s: rpm={r.registers[0]}, I={r.registers[1] / 100} A, flow={r.registers[2] / 10} m3/h")
    print(f"FC02 unit2 DI0..2 = {(await u2.read_discrete_inputs(0, 3, slave=2)).bits[:3]}")

    r = await u1.read_input_registers(100, 1, slave=1)
    print(f"illegal address IR100 -> error response: {r}")

    u1.close()
    u2.close()


if __name__ == "__main__":
    asyncio.run(main())
