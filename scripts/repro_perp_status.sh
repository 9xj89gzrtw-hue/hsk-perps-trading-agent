#!/usr/bin/env bash
# Finding 1: perpStatus is null on every registered perp market.
set -euo pipefail
python3 - <<'PY'
import json, urllib.request

URL = "https://hideki-dev.smk.somnia.host/v1/graphql"
Q = """query { Market(where: {perpRegistered: {_eq: true}}) {
  baseSymbol marketType perpRegistered perpStatus
} }"""
req = urllib.request.Request(URL, data=json.dumps({"query": Q}).encode(),
                             headers={"content-type": "application/json"})
rows = json.load(urllib.request.urlopen(req, timeout=30))["data"]["Market"]
nulls = [r for r in rows if r["perpStatus"] is None]
registered = [r for r in rows if r["perpRegistered"]]

print(f"rows                    {len(rows)}")
print(f"perpRegistered = true   {len(registered)}")
print(f"perpStatus = null       {len(nulls)}")
print()
if rows and len(nulls) == len(rows):
    print(f"BUG: perpStatus is null on all {len(rows)} registered perp markets")
    print("  expected  an enum a client can branch on (active / halted / …)")
    print(f"  actual    null on {len(nulls)}/{len(rows)} rows, while perpRegistered is true on all of them")
    print("  impact    a bot cannot tell a halted market from an active one")
    raise SystemExit(0)
print("OK: perpStatus carries a value on at least one market")
raise SystemExit(1)
PY
