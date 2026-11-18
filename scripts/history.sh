#!/usr/bin/env bash
# Historical data query: дані RRD4j через REST API openHAB.
# Використання: bash scripts/history.sh [item] [хвилин]
OH=${OH:-http://localhost:8084}
ITEM=${1:-Tank_Temperature}
MIN=${2:-30}
START=$(python -c "import datetime as d; print((d.datetime.now().astimezone()-d.timedelta(minutes=$MIN)).isoformat(timespec='seconds'))")

curl -s -G "$OH/rest/persistence/items/$ITEM" \
     --data-urlencode "serviceId=rrd4j" \
     --data-urlencode "starttime=$START" | python -c '
import json, sys, datetime as d
r = json.load(sys.stdin)
data = r.get("data", [])
vals = [float(p["state"]) for p in data]
print("%s: %d точок" % (r["name"], len(data)))
for p in data[-10:]:
    print("  ", d.datetime.fromtimestamp(p["time"] / 1000).strftime("%H:%M:%S"), p["state"])
if vals:
    print(f"min={min(vals):.2f} max={max(vals):.2f} avg={sum(vals)/len(vals):.2f}")'
