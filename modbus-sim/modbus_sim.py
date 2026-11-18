"""
Емулятор двох Modbus slave-пристроїв з простою фізичною моделлю процесу.

  Unit 1 "Резервуар з нагрівачем"  — Modbus TCP (MBAP),          порт 5020, slave id 1
  Unit 2 "Насос з частотником"     — Modbus RTU поверх TCP (CRC), порт 5021, slave id 2

Підтримувані функції: 01, 02, 03, 04, 05, 06, 15, 16 (усе, що вміє pymodbus-сервер).
Карта регістрів — у README.md (розділ "Modbus register map") і нижче в константах.
"""
import asyncio
import logging
import math
import os
import random
import struct

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

try:  # pymodbus >= 3.5
    from pymodbus.framer import Framer

    FRAMER_TCP, FRAMER_RTU = Framer.SOCKET, Framer.RTU
except ImportError:  # pragma: no cover - старіші версії
    from pymodbus.transaction import ModbusRtuFramer, ModbusSocketFramer

    FRAMER_TCP, FRAMER_RTU = ModbusSocketFramer, ModbusRtuFramer

log = logging.getLogger("modbus-sim")

FC_COIL, FC_DI, FC_HR, FC_IR = 1, 2, 3, 4

# ---------------- Unit 1: резервуар ----------------
U1_IR_TEMP = 0        # int16, x10, °C
U1_IR_LEVEL = 1       # uint16, x10, %
U1_IR_PRESSURE = 2    # uint16, x100, bar
U1_IR_STATUS = 3      # uint16, бітова маска стану
U1_IR_ENERGY = 4      # float32 (2 регістри, ABCD), kWh
U1_HR_TEMP_SP = 0     # int16, x10, °C
U1_HR_LEVEL_HI = 1    # uint16, x10, %
U1_HR_LEVEL_LO = 2    # uint16, x10, %
U1_CO_HEATER = 0
U1_CO_INLET = 1
U1_DI_LEVEL_HI = 0
U1_DI_LEVEL_LO = 1
U1_DI_OVERTEMP = 2

# ---------------- Unit 2: насос ----------------
U2_IR_SPEED = 0       # uint16, rpm
U2_IR_CURRENT = 1     # uint16, x100, A
U2_IR_FLOW = 2        # uint16, x10, m3/h
U2_IR_MOTOR_T = 3     # int16, x10, °C
U2_IR_RUNTIME = 4     # uint32 (2 регістри, hi-lo), секунди роботи
U2_HR_SPEED_SP = 0    # uint16, % (0..100)
U2_CO_RUN = 0
U2_CO_RESET = 1
U2_DI_RUNNING = 0
U2_DI_FAULT = 1
U2_DI_DRYRUN = 2

MAX_RPM = 2900
AMBIENT = 20.0


def i16(v: float) -> int:
    return int(round(v)) & 0xFFFF


def u16(v: float) -> int:
    return max(0, min(0xFFFF, int(round(v))))


def float32_words(v: float) -> list[int]:
    b = struct.pack(">f", v)
    return [int.from_bytes(b[0:2], "big"), int.from_bytes(b[2:4], "big")]


def uint32_words(v: int) -> list[int]:
    v &= 0xFFFFFFFF
    return [v >> 16, v & 0xFFFF]


def as_signed(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def make_slave(n: int = 16) -> ModbusSlaveContext:
    return ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [0] * n),
        co=ModbusSequentialDataBlock(0, [0] * n),
        hr=ModbusSequentialDataBlock(0, [0] * n),
        ir=ModbusSequentialDataBlock(0, [0] * n),
        zero_mode=True,  # регістр 0 = адреса 0 (як у openHAB readStart="0")
    )


class Process:
    """Фізична модель: нагрівач гріє воду, впускний клапан наповнює бак, насос його спорожнює."""

    def __init__(self, u1: ModbusSlaveContext, u2: ModbusSlaveContext):
        self.u1, self.u2 = u1, u2
        self.temp = 24.0
        self.level = 55.0
        self.energy = 0.0
        self.rpm = 0.0
        self.motor_t = 25.0
        self.runtime = 0
        self.fault = False
        self.dry_counter = 0
        # заводські налаштування (holding registers)
        u1.setValues(FC_HR, U1_HR_TEMP_SP, [i16(60.0 * 10)])
        u1.setValues(FC_HR, U1_HR_LEVEL_HI, [u16(90.0 * 10)])
        u1.setValues(FC_HR, U1_HR_LEVEL_LO, [u16(15.0 * 10)])
        u2.setValues(FC_HR, U2_HR_SPEED_SP, [70])

    def step(self, dt: float = 1.0) -> None:
        co1 = self.u1.getValues(FC_COIL, 0, 2)
        heater_cmd, inlet_open = bool(co1[U1_CO_HEATER]), bool(co1[U1_CO_INLET])
        lvl_hi = self.u1.getValues(FC_HR, U1_HR_LEVEL_HI, 1)[0] / 10
        lvl_lo = self.u1.getValues(FC_HR, U1_HR_LEVEL_LO, 1)[0] / 10

        co2 = self.u2.getValues(FC_COIL, 0, 2)
        run_cmd, reset_cmd = bool(co2[U2_CO_RUN]), bool(co2[U2_CO_RESET])
        speed_sp = max(0, min(100, self.u2.getValues(FC_HR, U2_HR_SPEED_SP, 1)[0]))

        # --- насос ---
        if reset_cmd:
            self.fault = False
            self.dry_counter = 0
            self.u2.setValues(FC_COIL, U2_CO_RESET, [0])  # імпульсна команда
            log.info("unit2: fault reset")
        running = run_cmd and not self.fault
        target = MAX_RPM * speed_sp / 100 if running else 0.0
        self.rpm += (target - self.rpm) * 0.35  # рампа частотника
        if self.rpm < 5:
            self.rpm = 0.0
        load = self.rpm / MAX_RPM
        flow = 12.0 * load if self.level > 0.5 else 0.0
        current = (0.4 + 7.6 * load**2) if self.rpm > 0 else 0.0
        dry = running and self.level < 5.0
        self.dry_counter = self.dry_counter + 1 if dry else 0
        if self.dry_counter >= 3 and not self.fault:
            self.fault = True
            log.warning("unit2: DRY-RUN fault, pump tripped")
        self.motor_t += (AMBIENT + 5 + 45 * load**2 - self.motor_t) * 0.03
        if self.rpm > 0:
            self.runtime += int(dt)

        # --- резервуар ---
        inflow = 0.9 if inlet_open else 0.0            # %/с
        self.level += (inflow - flow * 0.12) * dt
        self.level = max(0.0, min(100.0, self.level))
        overtemp = self.temp >= 85.0
        heater_on = heater_cmd and not overtemp and self.level > 5.0  # апаратне блокування
        heat = 0.45 if heater_on else 0.0
        cool = (self.temp - AMBIENT) * 0.008 + (0.02 * (self.temp - AMBIENT) / 10 if inlet_open else 0)
        self.temp += (heat - cool) * dt + random.uniform(-0.03, 0.03)
        if heater_on:
            self.energy += 3.0 * dt / 3600  # нагрівач 3 кВт
        pressure = 1.0 + self.level * 0.015 + 0.8 * load**2 + random.uniform(-0.005, 0.005)

        status = (
            (1 if heater_on else 0)
            | (2 if inlet_open else 0)
            | (4 if overtemp else 0)
            | (8 if self.level >= lvl_hi else 0)
            | (16 if self.level <= lvl_lo else 0)
        )

        # --- запис у регістри ---
        self.u1.setValues(FC_IR, U1_IR_TEMP, [i16(self.temp * 10), u16(self.level * 10), u16(pressure * 100), status])
        self.u1.setValues(FC_IR, U1_IR_ENERGY, float32_words(self.energy))
        self.u1.setValues(FC_DI, 0, [self.level >= lvl_hi, self.level <= lvl_lo, overtemp])

        self.u2.setValues(
            FC_IR,
            U2_IR_SPEED,
            [u16(self.rpm), u16(current * 100), u16(flow * 10), i16(self.motor_t * 10)],
        )
        self.u2.setValues(FC_IR, U2_IR_RUNTIME, uint32_words(self.runtime))
        self.u2.setValues(FC_DI, 0, [self.rpm > 0, self.fault, dry])


async def simulate(proc: Process, period: float) -> None:
    n = 0
    while True:
        try:
            proc.step(period)
        except Exception:  # модель не повинна валити сервер
            log.exception("simulation step failed")
        n += 1
        if n % 15 == 0:
            log.info(
                "T=%.1f°C L=%.1f%% rpm=%d fault=%s E=%.3fkWh",
                proc.temp, proc.level, proc.rpm, proc.fault, proc.energy,
            )
        await asyncio.sleep(period)


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    host = os.getenv("BIND", "0.0.0.0")
    tcp_port = int(os.getenv("TCP_PORT", "5020"))
    rtu_port = int(os.getenv("RTU_PORT", "5021"))
    u1, u2 = make_slave(), make_slave()
    proc = Process(u1, u2)
    proc.step(0)

    ctx1 = ModbusServerContext(slaves={1: u1}, single=False)
    ctx2 = ModbusServerContext(slaves={2: u2}, single=False)
    log.info("Unit 1 (Modbus TCP) on %s:%d, Unit 2 (Modbus RTU over TCP) on %s:%d", host, tcp_port, host, rtu_port)
    await asyncio.gather(
        StartAsyncTcpServer(context=ctx1, address=(host, tcp_port), framer=FRAMER_TCP),
        StartAsyncTcpServer(context=ctx2, address=(host, rtu_port), framer=FRAMER_RTU),
        simulate(proc, float(os.getenv("PERIOD", "1.0"))),
    )


if __name__ == "__main__":
    asyncio.run(main())
