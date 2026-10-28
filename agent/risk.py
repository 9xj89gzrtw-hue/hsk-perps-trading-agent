"""Position risk: liquidation, margin health, sizing on the venue's grid.

The formulas are for a linear, USDso-margined perp with isolated margin:
equity = margin + (entry - mark) * qty for a short, + for a long, and the
position is liquidated when equity equals the maintenance requirement
mmr * mark * qty. Fees and funding are excluded, so the result is the optimistic
liquidation price — the real one arrives earlier.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from .models import Market


def pnl(side: str, entry: Decimal, exit_price: Decimal, qty: Decimal) -> Decimal:
    if qty <= 0 or entry <= 0:
        return Decimal(0)
    move = exit_price - entry
    return move * qty if side == "long" else -move * qty


def maintenance_margin(price: Decimal, qty: Decimal, mmr: Decimal) -> Decimal:
    return price * qty * mmr


def margin_ratio(equity: Decimal, maintenance: Decimal) -> Decimal:
    """Equity / maintenance. Below 1.0 the position is liquidatable."""
    if maintenance <= 0:
        return Decimal("Infinity")
    return equity / maintenance


def liquidation_price(side: str, entry: Decimal, qty: Decimal, margin: Decimal, mmr: Decimal) -> Decimal | None:
    """Price at which equity meets maintenance, or None when it cannot happen.

    A position whose margin already sits below maintenance is liquidatable at
    the entry price; returning the entry makes that visible instead of hiding it
    behind a negative price.
    """
    if qty <= 0 or entry <= 0:
        return None
    per_unit_margin = margin / qty
    if side == "short":
        liq = (entry + per_unit_margin) / (Decimal(1) + mmr)
        return liq if liq > 0 else None
    liq = (entry - per_unit_margin) / (Decimal(1) - mmr)
    if liq <= 0:
        # The whole margin is gone before the price reaches zero: liquidated at 0.
        return Decimal(0)
    return liq


def liq_buffer(side: str, entry: Decimal, liq: Decimal | None) -> Decimal:
    """How far the mark can move against us, as a fraction of entry."""
    if liq is None or entry <= 0:
        return Decimal(0)
    return (liq - entry) / entry if side == "short" else (entry - liq) / entry


def max_notional(equity: Decimal, imr: Decimal) -> Decimal:
    """Largest notional the initial-margin gate allows, ignoring PnL and fees."""
    if imr <= 0:
        return Decimal(0)
    return equity / imr


def align_down(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def align_up(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def align_price(price: Decimal, tick: Decimal) -> Decimal:
    return align_down(price, tick) if tick > 0 else price


@dataclass(frozen=True)
class Sizing:
    qty: Decimal
    notional: Decimal
    margin: Decimal
    limited_by: str
    ok: bool


def size_order(
    margin: Decimal,
    price: Decimal,
    market: Market,
    leverage: Decimal,
    position_cap_qty: Decimal | None = None,
    slippage_bps: Decimal = Decimal(0),
) -> Sizing:
    """Size an order from the margin posted, capped and aligned DOWN.

    `leverage` can only lower the size below the market's own cap; it can never
    raise it above `1/imr`. The SDK's `getMaxPerpOrderSize` remains the
    authority — this is the arithmetic a caller can reason about offline.
    """
    if price <= 0 or margin <= 0:
        return Sizing(Decimal(0), Decimal(0), margin, "no-margin", False)
    max_lev = Decimal(1) / market.imr if market.imr > 0 else leverage
    effective_lev = min(leverage, max_lev)
    notional = margin * effective_lev
    limit = "leverage" if leverage <= max_lev else "initialMargin"
    if position_cap_qty is not None and position_cap_qty > 0:
        cap_notional = position_cap_qty * price
        if cap_notional < notional:
            notional = cap_notional
            limit = "maxPositionSize"
    if slippage_bps > 0:
        notional = notional * (Decimal(10_000) - slippage_bps) / Decimal(10_000)
    qty = align_down(notional / price, market.lot) if market.lot > 0 else notional / price
    if market.min_qty > 0 and qty < market.min_qty:
        return Sizing(Decimal(0), Decimal(0), margin, "minQuantity", False)
    return Sizing(qty=qty, notional=qty * price, margin=margin, limited_by=limit, ok=qty > 0)


def health(equity: Decimal, mark: Decimal, qty: Decimal, mmr: Decimal) -> Decimal:
    return margin_ratio(equity, maintenance_margin(mark, qty, mmr))


def top_up_needed(equity: Decimal, mark: Decimal, qty: Decimal, mmr: Decimal, target_ratio: Decimal) -> Decimal:
    """Extra margin to restore `target_ratio` at the current mark."""
    want = maintenance_margin(mark, qty, mmr) * target_ratio
    return max(Decimal(0), want - equity)
