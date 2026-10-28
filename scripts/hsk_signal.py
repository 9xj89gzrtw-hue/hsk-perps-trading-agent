#!/usr/bin/env python3
"""Publish and read one SignalAdapter row on a live chain — the end-to-end proof.

    HSK_CHAIN=133 HSK_PRIVATE_KEY=0x... python3 scripts/hsk_signal.py \
        --contract 0x... --key btc-funding --edge-bps 15720 --ttl 600

This is the read/write rehearsal for the contract the deploy gate funds. It does
four things and prints them as one JSON block, so the output can be pasted into a
submission as evidence: publish(), get(), isActionable(), and a second publish
under the same key to show that a super-*sede* leaves the previous row readable
in the event log but replaces the live one.

Reads need no key; `--read-only` skips the write.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import hsk  # noqa: E402
from agent.hsk import Rpc, chain_from_env, enc_bytes32, enc_int, enc_uint  # noqa: E402
from agent.keccak import selector  # noqa: E402


def key32(text: str) -> bytes:
    raw = text.encode()
    if len(raw) == 32:
        return raw
    return raw.ljust(32, b"\x00")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--chain", choices=["mainnet", "testnet"], default=None)
    ap.add_argument("--key", default="btc-funding")
    ap.add_argument("--edge-bps", type=int, default=15720)
    ap.add_argument("--ttl", type=int, default=900, help="seconds until the signal is stale")
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.chain:
        os.environ["HSK_CHAIN"] = args.chain
    chain = chain_from_env()
    rpc = Rpc(chain.rpc)
    k = key32(args.key)
    out: dict = {"chain": chain.chain_id, "contract": args.contract, "key": k.hex()}

    pub = rpc.call(args.contract, "0x" + selector("publisher()").hex())
    out["publisher"] = "0x" + pub[-40:]

    def read_state() -> dict:
        row = rpc.call(args.contract, "0x" + (selector("get(bytes32)") + enc_bytes32(k)).hex())
        word = len(row) // 2
        body = row[2:]
        # Signal is a 4-field static struct: int256, uint64, uint64, uint256
        return {
            "edge_bps": hsk.dec_int("0x" + body[0:64]),
            "published_at": int(body[64:128], 16),
            "expires_at": int(body[128:192], 16),
            "block_number": int(body[192:256], 16),
            "raw_words": word - 1,
        }

    def actionable() -> bool:
        raw = rpc.call(args.contract, "0x" + (selector("isActionable(bytes32)") + enc_bytes32(k)).hex())
        return hsk.decode_bool(raw)

    out["before"] = read_state()
    out["before_actionable"] = actionable()

    if not args.read_only:
        key_hex = os.environ.get("HSK_PRIVATE_KEY")
        if not key_hex:
            raise SystemExit("--read-only or HSK_PRIVATE_KEY")
        from eth_account import Account
        from eth_utils import to_checksum_address

        acct = Account.from_key(key_hex if key_hex.startswith("0x") else "0x" + key_hex)
        expires = int(time.time()) + args.ttl
        data = "0x" + (selector("publish(bytes32,int256,uint64)") + enc_bytes32(k)
                       + enc_int(args.edge_bps) + enc_uint(expires)).hex()
        nonce = int(rpc._post("eth_getTransactionCount", [acct.address, "pending"]), 16)
        try:
            estimate = rpc.estimate_gas({"from": acct.address, "to": to_checksum_address(args.contract), "data": data})
        except hsk.RpcError as exc:
            out["estimate_error"] = str(exc)
            estimate = 120_000
        tx = {
            "chainId": chain.chain_id, "nonce": nonce, "to": to_checksum_address(args.contract), "value": 0,
            "data": data, "gas": int(estimate * 13 // 10), "maxFeePerGas": 2 * 10 ** 9,
            "maxPriorityFeePerGas": 10 ** 9, "type": 2,
        }
        signed = Account.sign_transaction(tx, acct.key)
        raw = signed.raw_transaction.hex()
        raw = raw if raw.startswith("0x") else "0x" + raw
        out["tx"] = rpc._post("eth_sendRawTransaction", [raw])
        for _ in range(60):
            rc = rpc._post("eth_getTransactionReceipt", [out["tx"]])
            if rc:
                out["receipt"] = {"status": int(rc["status"], 16), "gas_used": int(rc["gasUsed"], 16),
                                  "block": int(rc["blockNumber"], 16)}
                break
            time.sleep(1)

        out["after"] = read_state()
        out["after_actionable"] = actionable()

    print(json.dumps(out, indent=2) if args.json else
          json.dumps({k: v for k, v in out.items() if k != "contract"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
