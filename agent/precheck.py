"""Pre-trade checks: the venue's own sizing, plus a decoder for its reverts.

`getMaxPerpOrderSize` is not a contract view — the SDK derives it from the
MarginBank, the pool parameters and the account's position with a binary
search. Re-implementing that in Python would be a second opinion the pool does
not share, so the authoritative answer comes from `bridge/precheck.mjs`, which
calls the SDK. The analytic path is a labelled fallback for machines without
Node, never a substitute.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .config import ERRORS, NAMES
from .models import Market

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "precheck.mjs"


@dataclass(frozen=True)
class Revert:
    selector: str | None
    name: str
    disposition: str

    @property
    def benign(self) -> bool:
        return self.name == "ImmediateOrCancelNoFill"


def decode_revert(payload: str) -> Revert | None:
    """Match a selector or an error name in a revert string / JSON blob."""
    text = payload or ""
    for selector, (name, disposition) in ERRORS.items():
        if selector.lower() in text.lower():
            return Revert(selector, name, disposition)
    for name, selector in NAMES.items():
        if name.lower() in text.lower():
            return Revert(selector, name, ERRORS[selector][1])
    match = re.search(r"0x[0-9a-fA-F]{8}", text)
    if match:
        return Revert(match.group(0), "UnknownRevert", "Capture the full trace and file a feedback report.")
    return None


@dataclass(frozen=True)
class Precheck:
    ok: bool
    priceable: bool
    placeable: bool
    max_quantity: Decimal
    max_quantity_raw: int
    limited_by: str
    source: str
    block: int
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "priceable": self.priceable,
            "placeable": self.placeable,
            "maxQuantity": str(self.max_quantity_raw),
            "maxQuantityHuman": str(self.max_quantity),
            "limitedBy": self.limited_by,
            "source": self.source,
            "block": self.block,
            "note": self.note,
        }


def analytic(market: Market, margin: Decimal, price: Decimal, leverage: Decimal, is_bid: bool) -> Precheck:
    """A lower bound: margin / imr / price, on the lot grid. Labelled as such."""
    from .risk import size_order

    if price <= 0:
        return Precheck(False, False, False, Decimal(0), 0, "stale-mark", "analytic", 0)
    sizing = size_order(margin, price, market, leverage)
    raw = int(sizing.qty * (Decimal(10) ** market.base_decimals))
    return Precheck(
        ok=sizing.ok,
        priceable=True,
        placeable=sizing.ok,
        max_quantity=sizing.qty,
        max_quantity_raw=raw,
        limited_by=sizing.limited_by,
        source="analytic",
        block=0,
        note="conservative bound; run bridge/precheck.mjs for the venue's number",
    )


def sdk(market: Market, account: str, price_raw: int, is_bid: bool, timeout: int = 90) -> Precheck | None:
    if not BRIDGE.exists() or shutil.which("node") is None:
        return None
    if not (ROOT / "bridge" / "node_modules").exists():
        return None
    cmd = [
        "node",
        str(BRIDGE),
        "--pool",
        market.pool,
        "--margin-bank",
        market.margin_bank,
        "--account",
        account,
        "--price",
        str(price_raw),
        "--is-bid",
        "true" if is_bid else "false",
        "--base-decimals",
        str(market.base_decimals),
    ]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    line = next((ln for ln in reversed(proc.stdout.strip().splitlines()) if ln.startswith("{")), None)
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not payload.get("ok"):
        return Precheck(
            False,
            bool(payload.get("priceable")),
            False,
            Decimal(0),
            0,
            str(payload.get("error") or "sdk-error"),
            "sdk",
            int(payload.get("asOfBlock") or 0),
            str(payload.get("detail") or "")[:200],
        )
    raw = int(payload.get("maxQuantity") or 0)
    return Precheck(
        ok=bool(payload.get("placeable")) and raw > 0,
        priceable=bool(payload.get("priceable")),
        placeable=bool(payload.get("placeable")),
        max_quantity=Decimal(raw) / (Decimal(10) ** market.base_decimals),
        max_quantity_raw=raw,
        limited_by=str(payload.get("limitedBy") or ""),
        source="sdk",
        block=int(payload.get("asOfBlock") or 0),
        note="",
    )


def precheck(market: Market, account: str, price: Decimal, margin: Decimal, leverage: Decimal, is_bid: bool) -> Precheck:
    price_raw = int(price * (Decimal(10) ** 18))
    venue = sdk(market, account, price_raw, is_bid)
    if venue is not None:
        return venue
    return analytic(market, margin, price, leverage, is_bid)
