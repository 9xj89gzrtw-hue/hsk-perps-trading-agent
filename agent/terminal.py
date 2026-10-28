"""Terminal rendering. Plain ANSI so it works over SSH and in CI logs."""

from __future__ import annotations

import os
from decimal import Decimal

from .funding import funding_view
from .models import Market, Snapshot
from .strategy import Plan

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
AMBER = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"


def color_enabled() -> bool:
    return os.environ.get("NO_COLOR") is None


def paint(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if color_enabled() else text


def _num(value: Decimal, places: int = 2) -> str:
    if value.is_infinite():
        return "inf"
    q = Decimal(1).scaleb(-places)
    return f"{value.quantize(q):,}"


def header(snapshot: Snapshot) -> str:
    return (
        paint("dreamDEX delta-neutral harvester", BOLD)
        + f"  ·  Hideki {50383}  ·  block {snapshot.block:,}  ·  {snapshot.at}  ·  {snapshot.source}"
    )


def funding_table(snapshot: Snapshot, limit: int = 12) -> str:
    rows = sorted(snapshot.perps, key=lambda m: -abs(m.funding_rate_raw))[:limit]
    out = [paint(f"{'market':12} {'mark':>12} {'APR%':>9} {'/int bps':>9} {'basis bps':>10} {'OI':>12}", DIM)]
    for m in rows:
        view = funding_view(m)
        apr = view.apr_percent
        tone = GREEN if apr >= 0 else RED
        per_interval_bps = view.per_interval * Decimal(10_000)
        out.append(
            f"{m.display:12} {_num(m.mark, 4):>12} "
            + paint(f"{apr:>+9.2f}", tone)
            + f" {per_interval_bps:>9.3f} {m.basis * Decimal(10_000):>+10.3f} {_num(m.open_interest, 2):>12}"
        )
    return "\n".join(out)


def pairs_table(plans: list[Plan]) -> str:
    out = [
        paint(
            f"{'pair':30} {'APR%':>8} {'basis':>8} {'notional':>11} {'day$':>9} {'BE h':>7} {'liq buf':>8}  status",
            DIM,
        )
    ]
    for p in plans:
        status = paint("ready", GREEN) if p.executable else paint(p.reasons[0][:34] if p.reasons else "no", AMBER)
        out.append(
            f"{p.pair.name:30} {p.apr_percent:>+8.2f} {p.basis * 100:>+7.3f}% {_num(p.notional):>11} "
            f"{_num(p.daily_carry, 3):>9} {_num(p.breakeven_hours, 1):>7} {p.liq_buffer * 100:>7.2f}%  {status}"
        )
    return "\n".join(out)


def plan_block(plan: Plan) -> str:
    lines = [
        paint("best plan", BOLD) + f"  {plan.pair.name}",
        f"  legs        short {plan.pair.perp.display} {_num(plan.perp_qty, 6)} @ {_num(plan.pair.perp.mark, 2)}"
        f"  |  long {plan.pair.spot.display} {_num(plan.spot_qty, 6)} @ {_num(plan.pair.spot.mark, 2)}",
        f"  capital     ${_num(plan.capital)}  ->  spot ${_num(plan.spot_cost)} + margin ${_num(plan.perp_margin)}"
        f"  (leverage {_num(plan.leverage, 2)}x, efficiency {_num(plan.capital_efficiency, 2)}x)",
        f"  carry       APR {plan.apr_percent:+.2f}%  ·  ${_num(plan.daily_carry, 3)}/day  ·  fees ${_num(plan.fee_cost, 3)}"
        f"  ·  break-even {_num(plan.breakeven_hours, 1)}h",
        f"  risk        liq ${_num(plan.liq_price or Decimal(0))} ({plan.liq_buffer * 100:+.2f}% away)"
        f"  ·  maintenance {plan.pair.perp.mmr * 100:.2f}%  ·  basis {plan.basis * 100:+.3f}%",
    ]
    if not plan.executable:
        lines.append(paint("  blocked     " + "; ".join(plan.reasons), AMBER))
    for note in plan.notes:
        lines.append(paint(f"  note        {note}", DIM))
    return "\n".join(lines)


def market_line(m: Market) -> str:
    view = funding_view(m)
    return (
        f"{m.display:12} mark {_num(m.mark, 4):>12}  funding {view.rate:+.6f}/{m.funding_window_sec // 3600}h"
        f"  APR {view.apr_percent:+.1f}%  oi {_num(m.open_interest, 1)}"
    )
