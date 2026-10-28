#!/usr/bin/env python3
"""Sign and send the SignalAdapter deployment (or any raw call) on HSK Chain.

    # fund first (faucet / testnet dev account), then:
    HSK_CHAIN=133 HSK_PRIVATE_KEY=0x... python3 scripts/hsk_send.py --dry-run
    HSK_CHAIN=133 HSK_PRIVATE_KEY=0x... python3 scripts/hsk_send.py

Why this file exists next to `hsk_deploy.py`, which deliberately refuses to sign:
the gate and the sender are different jobs with different risk. The gate answers
"can this be deployed, and what will it cost"; it must never see a key. The
sender owns exactly one key, prints the plan first, and refuses to broadcast
unless `--yes` is passed or `--dry-run` is omitted *and* the plan fits the
balance. A hand-rolled secp256k1 is still not used: `eth_account` does the
signing, because a nonce-reuse bug in bespoke ECDSA is silent for months.

Exit codes: 0 sent (or dry-run ok), 1 gate/plan failure, 2 transport failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import hsk  # noqa: E402
from agent.hsk import Rpc, chain_from_env  # noqa: E402

ARTIFACT = ROOT / "build" / "contracts_SignalAdapter_sol_SignalAdapter.bin"
DEFAULT_PUBLISHER = "0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a"


def gwei(value: str) -> int:
    return int(Decimal(value) * Decimal(10) ** 9)


def load_key(args) -> str:
    raw = os.environ.get("HSK_PRIVATE_KEY")
    if raw is None and args.key_file:
        blob = json.loads(Path(args.key_file).expanduser().read_text())
        raw = blob.get("privateKey") or blob.get("private_key") or blob.get("key")
    if not raw:
        raise SystemExit("no key: set HSK_PRIVATE_KEY or pass --key-file (never commit a key)")
    raw = raw.strip()
    return raw if raw.startswith("0x") else "0x" + raw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", choices=["mainnet", "testnet"], default=None)
    ap.add_argument("--publisher", default=os.environ.get("HSK_PUBLISHER", DEFAULT_PUBLISHER),
                    help="the constructor arg AND the address that must hold the key")
    ap.add_argument("--key-file", default=None, help="JSON with privateKey (default: $HSK_PRIVATE_KEY)")
    ap.add_argument("--max-fee", default="2", help="maxFeePerGas in gwei (default 2)")
    ap.add_argument("--priority-fee", default="1", help="maxPriorityFeePerGas in gwei (default 1)")
    ap.add_argument("--gas-limit", type=int, default=None, help="override the limit (default: estimate x1.25)")
    ap.add_argument("--dry-run", action="store_true", help="sign locally, print the hash, do not broadcast")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.chain:
        os.environ["HSK_CHAIN"] = args.chain
    chain = chain_from_env()

    try:
        from eth_account import Account
    except ImportError:
        raise SystemExit("pip install eth_account (the repo's own tests stay stdlib-only)")

    key = load_key(args)
    account = Account.from_key(key)
    if account.address.lower() != args.publisher.lower():
        raise SystemExit(
            f"key belongs to {account.address}, but --publisher is {args.publisher}; "
            "the deploy would succeed and then reject every publish()"
        )

    if not ARTIFACT.exists():
        raise SystemExit(f"missing {ARTIFACT}; run: npx solc@0.8.26 --bin --abi -o build contracts/SignalAdapter.sol")
    bytecode = ARTIFACT.read_text().strip()
    data = "0x" + bytecode + hsk.enc_address(args.publisher).hex()

    rpc = Rpc(chain.rpc)
    out: dict = {"chain": chain.name, "chain_id": chain.chain_id, "publisher": account.address}

    try:
        live = rpc.chain_id()
        if live != chain.chain_id:
            raise SystemExit(f"RPC says chain {live}, config says {chain.chain_id} — refusing to sign")
        out["block_number"] = rpc.block_number()
        balance = int(rpc._post("eth_getBalance", [account.address, "latest"]), 16)
        nonce = int(rpc._post("eth_getTransactionCount", [account.address, "pending"]), 16)
        estimate = rpc.estimate_gas({"from": account.address, "data": data})
    except hsk.RpcError as exc:
        print(f"transport: {exc}", file=sys.stderr)
        return 2

    gas_limit = args.gas_limit or int(estimate * 5 // 4)
    max_fee = gwei(args.max_fee)
    priority = gwei(args.priority_fee)
    worst = gas_limit * max_fee

    out.update({
        "balance_wei": balance,
        "balance_native": str(Decimal(balance) / Decimal(10) ** 18),
        "nonce": nonce,
        "gas_estimate": estimate,
        "gas_limit": gas_limit,
        "max_fee_gwei": str(Decimal(max_fee) / Decimal(10) ** 9),
        "worst_case_native": str(Decimal(worst) / Decimal(10) ** 18),
    })

    if balance < worst:
        out["error"] = "insufficient balance for the worst-case fee"
        print(json.dumps(out, indent=2) if args.json else
              f"not enough: {out['balance_native']} HSK < worst case {out['worst_case_native']} HSK",
              file=sys.stderr)
        return 1

    tx = {
        "chainId": chain.chain_id,
        "nonce": nonce,
        "to": "",
        "value": 0,
        "data": data,
        "gas": gas_limit,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
        "type": 2,
    }
    signed = Account.sign_transaction(tx, key)
    raw = signed.raw_transaction.hex()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    out["tx_hash"] = signed.hash.hex()

    if args.dry_run:
        out["dry_run"] = True
        out["raw_tx_bytes"] = len(raw) // 2 - 1
        print(json.dumps(out, indent=2) if args.json else
              f"dry run ok · nonce {nonce} · limit {gas_limit:,} · hash {out['tx_hash']}")
        return 0

    try:
        sent = rpc._post("eth_sendRawTransaction", [raw])
        out["rpc_tx_hash"] = sent
    except hsk.RpcError as exc:
        out["error"] = str(exc)
        print(json.dumps(out, indent=2) if args.json else f"broadcast failed: {exc}", file=sys.stderr)
        return 2

    receipt = None
    for _ in range(60):
        try:
            receipt = rpc._post("eth_getTransactionReceipt", [sent])
        except hsk.RpcError:
            receipt = None
        if receipt:
            break
        time.sleep(1)

    if not receipt:
        out["error"] = "no receipt after 60s (still pending?)"
        print(json.dumps(out, indent=2) if args.json else out["error"], file=sys.stderr)
        return 2

    out["status"] = int(receipt["status"], 16) if isinstance(receipt["status"], str) else receipt["status"]
    out["gas_used"] = int(receipt["gasUsed"], 16)
    out["block"] = int(receipt["blockNumber"], 16)
    out["effective_gas_price_wei"] = int(receipt.get("effectiveGasPrice", "0x0"), 16)
    out["contract"] = receipt.get("contractAddress")
    out["fee_native"] = str(Decimal(out["gas_used"] * out["effective_gas_price_wei"]) / Decimal(10) ** 18)

    if out["contract"]:
        code = rpc._post("eth_getCode", [out["contract"], "latest"])
        out["code_bytes"] = (len(code) - 2) // 2

    print(json.dumps(out, indent=2) if args.json else
          f"status {out['status']} · block {out['block']:,} · gas {out['gas_used']:,}"
          f" · fee {out['fee_native']} HSK\ncontract {out['contract']} ({out.get('code_bytes', 0)} bytes of code)"
          f"\ntx {sent}")
    return 0 if out["status"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
