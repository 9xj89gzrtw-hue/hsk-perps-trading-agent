"""Delta-neutral funding harvester: pair a perp with the spot it tracks.

The trade is cash-and-carry: buy the spot, short the perp, collect funding on
the short. Delta is flat only while both legs have the same size; the two legs
are NOT cross-margined, so a perp margin call can happen while the spot leg is
in profit. That is why every plan carries a liquidation price and a buffer and
why the venue's own `getMaxPerpOrderSize` still has the final say.

Only positive funding is executable here: the reverse trade would need a spot
short, which the venue does not offer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .funding import FundingView, funding_view
from .models import Market, Snapshot
from .risk import liq_buffer, liquidation_price, size_order

# Spot legs are not cross-margined with the perp and have no borrow market, so
# the only pairs we can actually hold are the ones the venue lists as spot.
HEDGE_ALIASES = {
    "BTC": ("WBTC", "BTC"),
    "ETH": ("WETH", "ETH"),
}


@dataclass(frozen=True)
class Pair:
    perp: Market
    spot: Market

    @property
    def name(self) -> str:
        return f"{self.perp.display} / {self.spot.display}"


@dataclass(frozen=True)
class Plan:
    pair: Pair
    notional: Decimal
    perp_qty: Decimal
    spot_qty: Decimal
    perp_margin: Decimal
    spot_cost: Decimal
    capital: Decimal
    funding: FundingView
    daily_carry: Decimal
    fee_cost: Decimal
    breakeven_hours: Decimal
    liq_price: Decimal | None
    liq_buffer: Decimal
    basis: Decimal
    score: Decimal
    executable: bool
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def apr_percent(self) -> Decimal:
        return self.funding.apr_percent

    @property
    def leverage(self) -> Decimal:
        return self.notional / self.perp_margin if self.perp_margin > 0 else Decimal(0)

    @property
    def capital_efficiency(self) -> Decimal:
        return self.notional / self.capital if self.capital > 0 else Decimal(0)


def hedge_for(perp: Market, spots: list[Market]) -> Market | None:
    aliases = HEDGE_ALIASES.get(perp.base.upper(), (perp.base.upper(),))
    for spot in spots:
        if spot.base.upper() in aliases:
            return spot
    return None


def build_pairs(perps: list[Market], spots: list[Market]) -> list[Pair]:
    pairs = []
    for perp in perps:
        spot = hedge_for(perp, spots)
        if spot is not None:
            pairs.append(Pair(perp=perp, spot=spot))
    return pairs


def plan_pair(
    pair: Pair,
    capital: Decimal,
    leverage: Decimal = Decimal(5),
    spot_fee_bps: Decimal = Decimal(10),
    min_apr: Decimal = Decimal("0.05"),
    max_breakeven_hours: Decimal = Decimal(72),
    min_liq_buffer: Decimal = Decimal("0.10"),
) -> Plan:
    perp, spot = pair.perp, pair.spot
    funding = funding_view(perp)
    price = perp.mark
    reasons: list[str] = []
    notes: list[str] = []

    if price <= 0:
        return Plan(pair, Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), capital, funding,
                    Decimal(0), Decimal(0), Decimal(0), None, Decimal(0), Decimal(0), Decimal(0), False,
                    ("no mark price",))

    # capital = spot notional + perp margin; margin = notional / leverage
    lev = leverage if leverage > 0 else Decimal(1)
    notional = capital / (Decimal(1) + Decimal(1) / lev)
    margin = notional / lev
    sizing = size_order(margin, price, perp, lev)
    if not sizing.ok:
        reasons.append(f"perp size below the venue grid ({sizing.limited_by})")
    notional = sizing.notional
    perp_qty = sizing.qty
    margin = sizing.margin

    spot_qty = notional / spot.mark if spot.mark > 0 else Decimal(0)
    if spot.min_qty > 0 and spot_qty < spot.min_qty:
        reasons.append(f"spot size {spot_qty:.8f} below minQuantity {spot.min_qty:.8f}")
    if spot.lot > 0:
        spot_qty = (spot_qty / spot.lot).to_integral_value(rounding="ROUND_DOWN") * spot.lot
    # Re-balance the hedge to the spot quantity we can actually buy.
    notional = spot_qty * spot.mark
    perp_qty = notional / price
    if perp.lot > 0:
        perp_qty = (perp_qty / perp.lot).to_integral_value(rounding="ROUND_DOWN") * perp.lot
    notional = perp_qty * price
    spot_qty = notional / spot.mark if spot.mark > 0 else Decimal(0)
    margin = notional / lev
    capital_used = notional + margin

    fees = notional * (spot_fee_bps + perp.taker_fee * Decimal(10_000)) / Decimal(10_000) * 2
    daily = notional * funding.daily
    hourly = daily / Decimal(24)
    breakeven = fees / hourly if hourly > 0 else Decimal("Infinity")

    liq = liquidation_price("short", price, perp_qty, margin, perp.mmr)
    buffer = liq_buffer("short", price, liq)

    if not funding.known:
        reasons.append("funding is not indexed yet")
    if funding.rate_raw <= 0:
        reasons.append("funding is negative: the reverse trade needs a spot short the venue does not offer")
    elif funding.apr < min_apr:
        reasons.append(f"APR {funding.apr_percent:.2f}% below the {min_apr * 100:.2f}% floor")
    if breakeven > max_breakeven_hours:
        reasons.append(f"fee break-even {breakeven:.1f}h above the {max_breakeven_hours:.0f}h limit")
    if buffer < min_liq_buffer:
        reasons.append(f"liquidation buffer {buffer * 100:.2f}% below {min_liq_buffer * 100:.0f}%")

    basis_pct = perp.basis * 100
    score = funding.apr_percent - abs(basis_pct) * 2 - (fees / notional * 100 if notional else Decimal(0))

    if buffer < Decimal("0.25"):
        notes.append("hedge profit is not cross-margin: top up the perp margin before the mark moves")
    if funding.interval_sec and funding.window_sec != funding.interval_sec:
        notes.append(
            f"rate is per {funding.window_sec // 3600}h window, settled every "
            f"{funding.interval_sec // 3600}h ({funding.window_sec // funding.interval_sec}x normalisation)"
        )

    return Plan(
        pair=pair,
        notional=notional,
        perp_qty=perp_qty,
        spot_qty=spot_qty,
        perp_margin=margin,
        spot_cost=spot_qty * spot.mark,
        capital=capital,
        funding=funding,
        daily_carry=daily,
        fee_cost=fees,
        breakeven_hours=breakeven,
        liq_price=liq,
        liq_buffer=buffer,
        basis=perp.basis,
        score=score,
        executable=not reasons,
        reasons=tuple(reasons),
        notes=tuple(notes),
    )


def rank(snapshot: Snapshot, capital: Decimal, leverage: Decimal = Decimal(5), **kwargs) -> list[Plan]:
    plans = [plan_pair(pair, capital, leverage, **kwargs) for pair in build_pairs(snapshot.perps, snapshot.spots)]
    return sorted(plans, key=lambda p: (p.executable, p.score), reverse=True)


def best(snapshot: Snapshot, capital: Decimal, leverage: Decimal = Decimal(5), **kwargs) -> Plan | None:
    plans = rank(snapshot, capital, leverage, **kwargs)
    return plans[0] if plans else None
