#!/usr/bin/env python3
"""Plan (and, when funded, hand off) the SignalAdapter deployment on HSK Chain.

    python3 scripts/hsk_deploy.py                      # live estimate + gate, no signing
    python3 scripts/hsk_deploy.py --chain testnet      # same against 133
    HSK_CHAIN=133 python3 scripts/hsk_deploy.py
    python3 scripts/hsk_deploy.py --json               # plan for cast / ethers

This script deliberately does not sign. Signing a mainnet deploy with a
hand-rolled secp256k1 implementation is exactly the kind of code that looks
correct for months and then leaks a key through a reused nonce. The plan below is
the input for an audited signer (`cast`, `ethers`, or the operator's own wallet),
and `--json` emits it in the shape those tools take.

The gate is the part that matters. It asks the node for `eth_estimateGas` on the
real bytecode and prices that at `base fee + tip`, with one explicit 1.25x margin.
It does not multiply the node's suggested price by two, and it does not carry a
1.3 HSK constant: on HSK the base fee is 500 gwei, so 1.3M gas * 2x looked like a
protocol fact when it was just two conservative guesses multiplied together.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import hsk  # noqa: E402
from agent.hsk import Rpc, chain_from_env, deployment_gate, gas_price  # noqa: E402

DEFAULT_PUBLISHER = "0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a"
BUILD = ROOT / "build"
ARTIFACT = BUILD / "contracts_SignalAdapter_sol_SignalAdapter.bin"
HSK_USD_ENV = "HSK_USD"


def constructor_args(publisher: str) -> str:
    return hsk.enc_address(publisher).hex()


def gwei(value: str) -> int:
    """Parse a decimal gwei string without float rounding."""
    return int(Decimal(value) * Decimal(10) ** 9)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", choices=["mainnet", "testnet"], default=None)
    ap.add_argument("--publisher", default=os.environ.get("HSK_PUBLISHER", DEFAULT_PUBLISHER))
    ap.add_argument("--gas-price", default=None, help="override the base fee, in gwei (e.g. 0.05)")
    ap.add_argument("--priority-fee", default="1", help="tip added to the base fee, in gwei (default 1)")
    ap.add_argument("--max-cost", default=None, help="refuse a plan above this many HSK")
    ap.add_argument("--json", action="store_true", help="emit the plan as JSON")
    args = ap.parse_args()

    if args.chain:
        os.environ["HSK_CHAIN"] = args.chain
    chain = chain_from_env()
    rpc = Rpc(chain.rpc)

    status: dict = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chain": {"name": chain.name, "chain_id": chain.chain_id, "rpc": chain.rpc, "native": chain.native},
        "publisher": args.publisher,
        "contract": "contracts/SignalAdapter.sol:SignalAdapter",
    }

    try:
        live_id = rpc.chain_id()
        status["rpc_chain_id"] = live_id
        status["rpc_matches_config"] = live_id == chain.chain_id
        status["block_number"] = rpc.block_number()
        status["gas_price_wei"] = rpc.gas_price()
        status["base_fee_wei"] = rpc.base_fee()
    except hsk.RpcError as exc:
        status["error"] = f"cannot reach {chain.rpc}: {exc}"
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print(f"unreachable: {exc}", file=sys.stderr)
        return 2

    if live_id != chain.chain_id:
        # Refuse to build a plan against a node whose chain id is not the one the
        # config names. This is the cheap check that catches a wrong RPC URL before
        # a transaction is sent to a chain nobody expected.
        status["error"] = f"RPC reports chain {live_id}, config says {chain.chain_id}"
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print(status["error"], file=sys.stderr)
        return 2

    # Build the calldata first: the gas estimate is only meaningful for the exact
    # bytes that will be sent, constructor argument included.
    if ARTIFACT.exists():
        bytecode = ARTIFACT.read_text().strip()
        deploy_data = "0x" + bytecode + constructor_args(args.publisher)
        status["artifact"] = {"path": str(ARTIFACT.relative_to(ROOT)), "bytes": len(bytecode) // 2}
        status["deploy_data"] = deploy_data
    else:
        deploy_data = None
        status["artifact"] = None
        status["note"] = "compile first: npx solc@0.8.26 --bin --abi -o build contracts/SignalAdapter.sol"

    estimate = hsk.DEPLOY_GAS_ESTIMATE
    estimate_source = "fallback"
    if deploy_data:
        try:
            estimate = rpc.estimate_gas({"from": args.publisher, "data": deploy_data})
            estimate_source = "eth_estimateGas"
        except hsk.RpcError as exc:
            status["estimate_error"] = str(exc)
    status["gas_estimate"] = estimate
    status["gas_estimate_source"] = estimate_source

    override = gwei(args.gas_price) if args.gas_price is not None else None
    price, base = gas_price(rpc, priority_fee_wei=gwei(args.priority_fee), override_wei=override)
    gate = deployment_gate(
        rpc,
        args.publisher,
        gas_estimate=estimate,
        gas_price_wei=override,
        priority_fee_wei=gwei(args.priority_fee),
    )

    usd = os.environ.get(HSK_USD_ENV)
    cost_native = gate.needed_native
    status["fee"] = {
        "base_fee_gwei": str(Decimal(base) / Decimal(10) ** 9),
        "priority_fee_gwei": str(Decimal(price - base) / Decimal(10) ** 9),
        "price_gwei": str(Decimal(price) / Decimal(10) ** 9),
        "gas_estimate": estimate,
        "gas_estimate_source": estimate_source,
        "margin_bps": hsk.GAS_MARGIN_BPS,
        "cost_native": str(cost_native),
        "cost_usd": str((cost_native * Decimal(usd)).quantize(Decimal("0.0001"))) if usd else None,
        "max_fee_per_gas_wei": price,
        "gas_limit_suggested": int(estimate * 12 // 10),
    }
    status["deploy_gate"] = {
        "ready": gate.ready,
        "reason": gate.reason,
        "balance_native": str(gate.balance_native),
        "needed_native": str(gate.needed_native),
        "shortfall_native": str(gate.shortfall_native),
    }
    status["next_step"] = (
        "fund the publisher, then send deploy_data from it"
        if not gate.ready
        else "funded: send deploy_data from the publisher address"
    )

    max_cost = Decimal(args.max_cost) if args.max_cost else None
    if max_cost is not None and cost_native > max_cost:
        status["deploy_gate"]["ready"] = False
        status["deploy_gate"]["reason"] = f"cost {cost_native:.6f} HSK above --max-cost {max_cost}"
        gate = hsk.DeployGate(False, status["deploy_gate"]["reason"], gate.balance_wei, gate.needed_wei)

    if args.json:
        print(json.dumps(status, indent=2))
        return 0 if gate.ready else 1

    fee = status["fee"]
    print(f"chain      {chain.name} ({chain.chain_id})  block {status['block_number']:,}")
    print(f"publisher  {args.publisher}")
    print(
        f"gas        base {fee['base_fee_gwei']} gwei + tip {fee['priority_fee_gwei']} gwei"
        f" = {fee['price_gwei']} gwei"
    )
    print(
        f"estimate   {estimate:,} gas ({estimate_source})"
        f"  ·  cost {cost_native:.6f} {chain.native}"
        + (f" (~${fee['cost_usd']})" if fee["cost_usd"] else "")
    )
    print(f"artifact   {status['artifact']['bytes'] if status['artifact'] else 'MISSING'} bytes")
    print(f"gate       {gate.line(chain)}")
    if live_id == 133 and not gate.ready:
        print()
        print("testnet is 1 gwei: a deploy here costs under 0.001 HSK. The faucet is")
        print("https://faucet.hsk.xyz (chain 133) — no mainnet value is required to test.")
    print()
    if not gate.ready:
        print("NOT SUBMITTABLE: a hackathon entry that requires a mainnet deployment")
        print("is not a submission until this gate is green. Fund the publisher above.")
        return 1
    print("ready. deploy_data is in --json output; send it with an audited signer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
