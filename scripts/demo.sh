#!/usr/bin/env bash
# Сценарій для demo video: керування через REST (= кнопки sitemap), аварії, втрата зв'язку.
OH=${OH:-http://localhost:8084}
cmd()  { curl -s -X POST -H "Content-Type: text/plain" -d "$2" "$OH/rest/items/$1" >/dev/null; echo "  -> $1 := $2"; }
show() { for i in "$@"; do printf "     %-18s %s\n" "$i" "$(curl -s "$OH/rest/items/$i/state")"; done; }
step() { echo; echo "### $*"; }

step "1. Ручний режим: пуск насоса 100 %, клапан закрито — рівень падає"
cmd Auto_Mode OFF; cmd Tank_InletValve OFF; cmd Pump_SpeedSetpoint 100; cmd Pump_Run ON
sleep 5; show Pump_Speed Pump_Current Pump_Flow Tank_Level

step "2. Чекаємо сухого ходу (рівень < 5 %) -> аварія насоса (до ~60 с)"
for _ in $(seq 1 30); do
  [ "$(curl -s "$OH/rest/items/Pump_Fault/state")" = "ON" ] && break; sleep 2
done
show Tank_Level Pump_Fault Pump_DryRun Alarm_LowLevel Alarm_PumpFault Alarm_ActiveCount Alarm_Last

step "3. Скидання аварії та автоматичний режим (термостат + підтримка рівня + блокування насоса)"
cmd Pump_FaultReset ON; cmd Tank_TempSetpoint 55; cmd Auto_Mode ON; cmd Alarm_Ack ON
sleep 6; show Pump_Fault Tank_Heater Tank_InletValve Tank_Level Tank_Temperature

step "4. Втрата зв'язку з Modbus-пристроями -> якість BAD_COMM, аварії зв'язку, OPC UA StatusCode=Bad"
docker stop iotpr12-modbus-sim >/dev/null; sleep 35
show Unit1_Quality Unit2_Quality Alarm_CommUnit1 Alarm_CommUnit2

step "5. Відновлення зв'язку"
docker start iotpr12-modbus-sim >/dev/null; sleep 15
show Unit1_Quality Unit2_Quality Alarm_CommUnit1 Tank_Temperature
