#!/usr/bin/env python3
"""Plan (and, when funded, hand off) the SignalAdapter deployment on HSK Chain.

    python3 scripts/hsk_deploy.py                      # gate + plan, no signing
    python3 scripts/hsk_deploy.py --chain testnet      # same against 133
    HSK_CHAIN=133 python3 scripts/hsk_deploy.py

This script deliberately does not sign. Signing a mainnet deploy with a
hand-rolled secp256k1 implementation is exactly the kind of code that looks
correct for months and then leaks a key through a reused nonce. The plan below is
the input for an audited signer (`cast`, `ethers`, or the operator's own wallet),
and `--json` emits it in the shape those tools take.

What the script does decide, and decides before anything is signed, is whether the
account can pay for the deploy at all. An unfunded deploy does not fail loudly: it
burns the gas it has and leaves no contract, which is how "deployed on mainnet"
claims end up untrue.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import hsk  # noqa: E402
from agent.hsk import Rpc, chain_from_env, deployment_gate  # noqa: E402

DEFAULT_PUBLISHER = "0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a"
BUILD = ROOT / "build"
ARTIFACT = BUILD / "contracts_SignalAdapter_sol_SignalAdapter.bin"


def constructor_args(publisher: str) -> str:
    return hsk.enc_address(publisher).hex()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", choices=["mainnet", "testnet"], default=None)
    ap.add_argument("--publisher", default=os.environ.get("HSK_PUBLISHER", DEFAULT_PUBLISHER))
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

    gate = deployment_gate(rpc, args.publisher)
    status["deploy_gate"] = {
        "ready": gate.ready,
        "reason": gate.reason,
        "balance_native": str(gate.balance_native),
        "needed_native": str(hsk.Decimal(gate.needed_wei) / hsk.WEI),
        "shortfall_native": str(gate.shortfall_native),
        "gas_estimate": hsk.DEPLOY_GAS_ESTIMATE,
    }

    if ARTIFACT.exists():
        bytecode = ARTIFACT.read_text().strip()
        status["artifact"] = {"path": str(ARTIFACT.relative_to(ROOT)), "bytes": len(bytecode) // 2}
        status["deploy_data"] = "0x" + bytecode + constructor_args(args.publisher)
    else:
        status["artifact"] = None
        status["note"] = "compile first: npx solc@0.8.26 --bin --abi -o build contracts/SignalAdapter.sol"

    status["next_step"] = (
        "fund the publisher, then send deploy_data from it"
        if not gate.ready
        else "funded: send deploy_data from the publisher address"
    )

    if args.json:
        print(json.dumps(status, indent=2))
        return 0 if gate.ready else 1

    print(f"chain      {chain.name} ({chain.chain_id})  block {status['block_number']:,}")
    print(f"publisher  {args.publisher}")
    print(f"gas price  {status['gas_price_wei'] / 1e9:.6f} gwei")
    print(f"artifact   {status['artifact']['bytes'] if status['artifact'] else 'MISSING'} bytes")
    print(f"gate       {gate.line(chain)}")
    print()
    if not gate.ready:
        print("NOT SUBMITTABLE: a hackathon entry that requires a mainnet deployment")
        print("is not a submission until this gate is green. Fund the publisher above.")
        return 1
    print("ready. deploy_data is in --json output; send it with an audited signer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
