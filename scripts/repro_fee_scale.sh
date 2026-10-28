#!/usr/bin/env bash
# Finding 4: takerFeeBpsTimes1k has no documented scale, and the obvious reading is 100x off.
set -euo pipefail
python3 - <<'PY'
import json, urllib.request
from decimal import Decimal

URL = "https://hideki-dev.smk.somnia.host/v1/graphql"
Q = """query { PerpPoolParametersEpoch(order_by: {timestamp: desc}, limit: 100) {
  pool takerFeeBpsTimes1k makerFeeBpsTimes1k
} }"""
req = urllib.request.Request(URL, data=json.dumps({"query": Q}).encode(),
                             headers={"content-type": "application/json"})
rows = json.load(urllib.request.urlopen(req, timeout=30))["data"]["PerpPoolParametersEpoch"]

seen, errors = {}, 0
for row in rows:
    pool = str(row["pool"]).lower()
    if pool in seen:
        continue
    seen[pool] = row
    raw = int(row["takerFeeBpsTimes1k"] or 0)
    # naive: treat the field as bps x 100, i.e. 100_000 -> 1.0 (100%)
    if Decimal(raw) / Decimal(100_000) > Decimal("0.01"):
        errors += 1

nonzero = [r for r in seen.values() if int(r["takerFeeBpsTimes1k"] or 0) != 0]
builder_cap = 100_000

print(f"pools                   {len(seen)}")
print(f"non-zero taker fee      {len(nonzero)}")
print(f"reference               getMaxBuilderFeeBpsTimes1k() = {builder_cap} is documented as a 1% cap")
print(f"implied scale           fraction = raw / 10^7")
print(f"naive reading           raw / 10^5 -> {Decimal(builder_cap) / Decimal(100_000)} (100%, not 1%)")
print()

if not nonzero:
    print("BUG: fee scale is only inferable from the mainnet builder cap")
    print("  expected  the unit is stated on the field, and a testnet market exercises a non-zero fee")
    print("  actual    every testnet market reports 0, so a 100x error cannot be caught locally")
    print(f"  impact    naive raw/1e5 reads {builder_cap} as 100%, raw/1e7 reads it as 1%")
    raise SystemExit(0)
print("OK: at least one market charges a fee, so the scale can be observed")
raise SystemExit(1)
PY
