#!/usr/bin/env bash
# Finding 2: fundingRate covers fundingWindowSec, not fundingIntervalSec.
set -euo pipefail
python3 - <<'PY'
import json, urllib.request
from decimal import Decimal

URL = "https://hideki-dev.smk.somnia.host/v1/graphql"
Q = """query { Market(where: {baseSymbol: {_eq: "BTC"}, marketType: {_eq: "PERP"}}, limit: 1) {
  baseSymbol fundingRate fundingWindowSec fundingIntervalSec
} }"""
req = urllib.request.Request(URL, data=json.dumps({"query": Q}).encode(),
                             headers={"content-type": "application/json"})
row = json.load(urllib.request.urlopen(req, timeout=30))["data"]["Market"][0]

rate = Decimal(row["fundingRate"]) / Decimal(10**18)
window = Decimal(row["fundingWindowSec"])
interval = Decimal(row["fundingIntervalSec"])
year = Decimal(31_536_000)

correct = rate * year / window * 100
naive = rate * year / interval * 100

print(f"{row['baseSymbol']}-PERP  rate {rate} per window")
print(f"  fundingWindowSec      {int(window)}")
print(f"  fundingIntervalSec    {int(interval)}")
print(f"  correct   APR  {correct:.2f}%   (rate × 3 windows/day × 365)")
print(f"  naive     APR  {naive:.2f}%   (rate × 24 intervals/day × 365)")
print(f"  ratio     {naive / correct:.2f}×")
print()
if window != interval:
    print("BUG: fundingRate covers fundingWindowSec, not fundingIntervalSec")
    print("  expected  the denominator is documented next to the field")
    print("  actual    both fields are exposed, neither is documented as the denominator")
    print("  impact    annualising per interval overstates APR by window/interval")
    raise SystemExit(0)
print("OK: the two windows are equal on this market")
raise SystemExit(1)
PY
