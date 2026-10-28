"""HSK Chain: the venue's chain, its gas, and the signal contract's plumbing.

Everything here is stdlib. The point is not to wrap a web3 library -- it is that a
reviewer can read the whole transaction path, from the RPC call to the 4-byte
selector, without installing anything.

What this module deliberately does NOT do is invent addresses. HSKswap routers and
stablecoin addresses differ per deployment and are not published in a form this
repo could verify offline, so they come from config (`HSK_*` env vars) and the
adapter refuses to build a trade against an unset one. A guessed router address on
mainnet is a way to lose a position to a look-alike contract.

Chain facts below were read from the live RPC, not copied from a blog:

    eth_chainId -> 0xb1 (177)     https://mainnet.hsk.xyz
    eth_chainId -> 0x85 (133)     https://testnet.hsk.xyz
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

from .keccak import keccak256, selector, topic

WEI = Decimal(10) ** 18


@dataclass(frozen=True)
class Chain:
    name: str
    chain_id: int
    rpc: str
    explorer: str
    native: str


# Verified live on 2026-10-28: both answered eth_chainId with the id below.
HSK_MAINNET = Chain(
    name="hsk",
    chain_id=177,
    rpc="https://mainnet.hsk.xyz",
    explorer="https://explorer.hsk.xyz",
    native="HSK",
)

HSK_TESTNET = Chain(
    name="hsk-testnet",
    chain_id=133,
    rpc="https://testnet.hsk.xyz",
    explorer="https://explorer-testnet.hsk.xyz",
    native="HSK",
)

CHAINS = {c.chain_id: c for c in (HSK_MAINNET, HSK_TESTNET)}


def chain_from_env(default: Chain = HSK_MAINNET) -> Chain:
    """HSK_CHAIN=177|133|mainnet|testnet picks the target; anything else is an error.

    Not a fallback. Silently defaulting to testnet after a typo is how a "deployed"
    project turns out to have deployed nothing.
    """
    raw = os.environ.get("HSK_CHAIN", "").strip().lower()
    if not raw:
        return default
    aliases = {"mainnet": 177, "hsk": 177, "testnet": 133, "hsk-testnet": 133}
    if raw in aliases:
        return CHAINS[aliases[raw]]
    try:
        return CHAINS[int(raw)]
    except (KeyError, ValueError):
        raise ValueError(f"HSK_CHAIN={raw!r} is not 177, 133, mainnet or testnet") from None


class RpcError(RuntimeError):
    def __init__(self, method: str, payload: dict):
        self.method = method
        self.payload = payload
        err = payload.get("error") or {}
        super().__init__(f"{method}: {err.get('message') or payload}")


class Rpc:
    """Minimal JSON-RPC over urllib, with a retry that distinguishes 'slow' from 'rejected'."""

    # HSK's public endpoint answers 403 to urllib's default Python-urllib/3.x agent
    # while accepting the same request from curl. The header is not cosmetic: without
    # it every call looks like a transport failure and a deploy gate reads as "no
    # balance" instead of "cannot reach the node".
    USER_AGENT = "hsk-perps-agent/1.0 (+https://github.com/9xj89gzrtw-hue/hsk-perps-trading-agent)"

    def __init__(self, url: str, timeout: float = 20.0, retries: int = 2):
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self._id = 0

    def _post(self, method: str, params: list) -> dict:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode()
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                self.url,
                data=body,
                headers={
                    "content-type": "application/json",
                    "accept": "application/json",
                    "user-agent": self.USER_AGENT,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read())
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise RpcError(method, {"error": {"message": str(exc)}}) from exc
            if "error" in payload:
                # A revert or a method the node does not have is an answer, not a
                # transport failure: retrying it just burns time.
                raise RpcError(method, payload)
            return payload.get("result")
        raise RpcError(method, {"error": {"message": str(last)}})

    def chain_id(self) -> int:
        return int(self._post("eth_chainId", []), 16)

    def block_number(self) -> int:
        return int(self._post("eth_blockNumber", []), 16)

    def gas_price(self) -> int:
        return int(self._post("eth_gasPrice", []), 16)

    def balance(self, address: str) -> int:
        return int(self._post("eth_getBalance", [address, "latest"]), 16)

    def nonce(self, address: str) -> int:
        return int(self._post("eth_getTransactionCount", [address, "latest"]), 16)

    def call(self, to: str, data: str) -> str:
        return self._post("eth_call", [{"to": to, "data": data}, "latest"])

    def estimate_gas(self, tx: dict) -> int:
        return int(self._post("eth_estimateGas", [tx]), 16)


# --- ABI -------------------------------------------------------------------
#
# Only the pieces this contract needs. A full encoder is a liability here: every
# branch is a place to get a word wrong, and the tests would have to trust it.

def pad32(raw: bytes) -> bytes:
    if len(raw) > 32:
        raise ValueError("word longer than 32 bytes")
    return raw.rjust(32, b"\x00")


def enc_uint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative uint")
    return value.to_bytes(32, "big")


def enc_int(value: int) -> bytes:
    """Two's complement over 32 bytes, the only encoding the ABI accepts for int256."""
    return (value % (1 << 256)).to_bytes(32, "big")


def enc_address(address: str) -> bytes:
    text = address[2:] if address[:2] in ("0x", "0X") else address
    if len(text) != 40:
        raise ValueError(f"not an address: {address!r}")
    return pad32(bytes.fromhex(text))


def enc_bytes32(value: bytes) -> bytes:
    if len(value) != 32:
        raise ValueError("bytes32 is exactly 32 bytes")
    return value


def dec_uint(word: str) -> int:
    return int(word, 16)


def dec_int(word: str) -> int:
    raw = int(word, 16)
    return raw - (1 << 256) if raw >= (1 << 255) else raw


PUBLISH_SELECTOR = selector("publish(bytes32,int256,uint64)")
REVOKE_SELECTOR = selector("revoke(bytes32)")
GET_SELECTOR = selector("get(bytes32)")
IS_ACTIONABLE_SELECTOR = selector("isActionable(bytes32)")
SIGNAL_PUBLISHED_TOPIC = topic("SignalPublished(bytes32,int256,uint64,uint256)")

SIGNAL_ADAPTER_ABI = [
    {"type": "function", "name": "publish", "stateMutability": "nonpayable",
     "inputs": [{"name": "key", "type": "bytes32"}, {"name": "edgeBps", "type": "int256"},
                {"name": "expiresAt", "type": "uint64"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "revoke", "stateMutability": "nonpayable",
     "inputs": [{"name": "key", "type": "bytes32"}], "outputs": []},
    {"type": "function", "name": "isActionable", "stateMutability": "view",
     "inputs": [{"name": "key", "type": "bytes32"}], "outputs": [{"name": "", "type": "bool"}]},
]

# Gas measured for the deploy and for one publish, with headroom. The deploy number
# is the cost of the contract above (~1.1M) plus a margin; it is an estimate and the
# gate below says so, rather than pretending to be a quote.
DEPLOY_GAS_ESTIMATE = 1_300_000
PUBLISH_GAS_ESTIMATE = 90_000


def signal_key(label: str) -> bytes:
    """A stable 32-byte key from a human label, so the key is reproducible in a review."""
    return keccak256(label.encode("utf-8"))


def encode_publish(key: bytes, edge_bps: int, expires_at: int) -> str:
    return "0x" + (PUBLISH_SELECTOR + enc_bytes32(key) + enc_int(edge_bps) + enc_uint(expires_at)).hex()


def encode_revoke(key: bytes) -> str:
    return "0x" + (REVOKE_SELECTOR + enc_bytes32(key)).hex()


def encode_is_actionable(key: bytes) -> str:
    return "0x" + (IS_ACTIONABLE_SELECTOR + enc_bytes32(key)).hex()


def decode_bool(word: str) -> bool:
    return int(word, 16) != 0


@dataclass(frozen=True)
class SignalRow:
    edge_bps: int
    published_at: int
    expires_at: int
    block_number: int

    @property
    def live(self) -> bool:
        return self.published_at != 0 and self.expires_at > 0


def decode_signal(row: str) -> SignalRow:
    """get(bytes32) returns the struct as four static words, in declaration order."""
    body = row[2:] if row.startswith("0x") else row
    words = [body[i:i + 64] for i in range(0, 256, 64)]
    if len(words) < 4:
        raise ValueError("short struct: get() returned fewer than four words")
    return SignalRow(
        edge_bps=dec_int(words[0]),
        published_at=dec_uint(words[1]),
        expires_at=dec_uint(words[2]),
        block_number=dec_uint(words[3]),
    )


# --- Deployment gate -------------------------------------------------------

@dataclass(frozen=True)
class DeployGate:
    ready: bool
    reason: str
    balance_wei: int
    needed_wei: int

    @property
    def balance_native(self) -> Decimal:
        return Decimal(self.balance_wei) / WEI

    @property
    def shortfall_native(self) -> Decimal:
        return max(Decimal(0), Decimal(self.needed_wei - self.balance_wei) / WEI)

    def line(self, chain: Chain) -> str:
        if self.ready:
            return f"ready: {self.balance_native:.6f} {chain.native} covers ~{Decimal(self.needed_wei)/WEI:.6f}"
        return (
            f"blocked: {self.balance_native:.6f} {chain.native} on {chain.name} (chain {chain.chain_id}), "
            f"need ~{Decimal(self.needed_wei)/WEI:.6f}, short {self.shortfall_native:.6f} {chain.native}"
        )


def deployment_gate(rpc: Rpc, address: str, gas_estimate: int = DEPLOY_GAS_ESTIMATE) -> DeployGate:
    """Decide, before anything is signed, whether this account can pay for the deploy.

    The fee is priced at 2x the current base fee because HSK, like most EVM chains,
    can move the base fee between the estimate and the inclusion, and a deploy that
    runs out of gas burns the fee and leaves nothing behind.
    """
    price = rpc.gas_price() * 2
    needed = price * gas_estimate
    balance = rpc.balance(address)
    if balance == 0:
        return DeployGate(False, "empty account: no gas token at all", balance, needed)
    if balance < needed:
        return DeployGate(False, "underfunded for the deploy", balance, needed)
    return DeployGate(True, "funded", balance, needed)
