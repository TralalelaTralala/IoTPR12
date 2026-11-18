#!/usr/bin/env bash
# Стан шлюзу через REST API openHAB: Things (Modbus) та ключові items.
OH=${OH:-http://localhost:8084}

echo "== Modbus devices"
for i in Unit1_Quality Unit1_LastRead Unit1_LastError Unit2_Quality Unit2_LastRead Unit2_LastError; do
  printf "  %-20s %s
" "$i" "$(curl -s "$OH/rest/items/$i/state")"
done

echo "== Items"
for i in Tank_Temperature Tank_Level Tank_Pressure Tank_Energy Tank_TempSetpoint Tank_Heater Tank_InletValve \
         Pump_Speed Pump_Current Pump_Flow Pump_RunTime Pump_Run Pump_Fault \
         Alarm_ActiveCount Alarm_Unacked Alarm_Last OPCUA_Status OPCUA_Clients; do
  printf "  %-20s %s\n" "$i" "$(curl -s "$OH/rest/items/$i/state")"
done
