#!/usr/bin/env bash
# Finding 3: the docs' testnet chain id (50312) is not the perps chain (50383).
set -euo pipefail
python3 - <<'PY'
import json, urllib.request

RPC = "https://api.hideki2.infra.testnet.somnia.network"
DOCUMENTED_TESTNET = 50312
DOCUMENTED_MAINNET = 5031

def call(method, params):
    req = urllib.request.Request(
        RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))["result"]

chain_id = int(call("eth_chainId", []), 16)
client = call("web3_clientVersion", [])

print(f"RPC                    {RPC}")
print(f"eth_chainId            {hex(chain_id)} = {chain_id}")
print(f"web3_clientVersion     {client}")
print(f"docs, mainnet          {DOCUMENTED_MAINNET}")
print(f"docs, testnet          {DOCUMENTED_TESTNET}")
print()
if chain_id != DOCUMENTED_TESTNET:
    print(f"BUG: docs say testnet is {DOCUMENTED_TESTNET}, the perps RPC answers {chain_id}")
    print("  expected  one testnet id that matches the deployment the SDK calls hidekiTestnet")
    print(f"  actual    RPC {chain_id}, docs {DOCUMENTED_TESTNET} (and mainnet {DOCUMENTED_MAINNET})")
    print("  impact    SIWE login built from the docs signs the wrong chain id")
    raise SystemExit(0)
print("OK: the RPC matches the documented testnet id")
raise SystemExit(1)
PY
