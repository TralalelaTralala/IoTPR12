#!/usr/bin/env bash
# Запис демо-відео: скидає стан процесу, запускає Playwright-рекордер у мережі стека
# і виконує docker stop/start емулятора за сигналами рекордера.
set -e
cd "$(dirname "$0")/.."
OH=http://localhost:8084
cmd() { curl -s -X POST -H "Content-Type: text/plain" -d "$2" "$OH/rest/items/$1" >/dev/null; }
st()  { curl -s "$OH/rest/items/$1/state"; }

echo "== reset process state"
cmd Auto_Mode OFF; cmd Pump_Run OFF; cmd Tank_Heater OFF; cmd Tank_InletValve OFF
docker restart iotpr12-modbus-sim >/dev/null          # свіжа модель: T≈24 °C, L=55 %, SP=60 °C
for _ in $(seq 1 30); do [ "$(st Unit1_Quality)$(st Unit2_Quality)" = GOODGOOD ] && break; sleep 2; done
cmd Tank_Heater ON; cmd Alarm_Ack ON; sleep 2
echo "   quality: $(st Unit1_Quality)/$(st Unit2_Quality)  T=$(st Tank_Temperature)  L=$(st Tank_Level)"

OUT="$(pwd)/video/out"; rm -f "$OUT"/signal "$OUT"/*.webm
docker build -q -t iotpr12-recorder video/recorder >/dev/null

( while true; do
    if [ -f "$OUT/signal" ]; then
      s=$(cat "$OUT/signal"); rm -f "$OUT/signal"
      echo "   host: docker $s modbus-sim"; docker "$s" iotpr12-modbus-sim >/dev/null
    fi
    sleep 0.3
  done ) &
LOOP=$!
trap 'kill $LOOP 2>/dev/null' EXIT

MSYS_NO_PATHCONV=1 docker run --rm --network iotpr12_scada --shm-size=1g \
  -v "$(cygpath -w "$OUT" 2>/dev/null || echo "$OUT"):/out" iotpr12-recorder
