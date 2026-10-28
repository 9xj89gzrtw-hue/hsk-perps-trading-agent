"""A self-contained SVG state card: no browser, no fonts, no network.

The card is the artifact that makes a run reviewable after the terminal is
gone: one glance shows the market universe, the chosen pair, the carry, and the
liquidation buffer, plus the block the numbers came from.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Iterable

from .funding import funding_view
from .models import Market, Snapshot
from .strategy import Plan

W, H = 1200, 660
BG = "#0b0f14"
PANEL = "#121922"
INK = "#e8eef7"
MUTED = "#8494a8"
GREEN = "#39d98a"
RED = "#ff6b6b"
AMBER = "#f5b84b"
BLUE = "#5aa9ff"


def _fmt(value: Decimal, places: int = 2) -> str:
    if value.is_infinite():
        return "∞"
    q = Decimal(1).scaleb(-places)
    return f"{value.quantize(q):,}"


def _pct(value: Decimal, places: int = 2) -> str:
    return f"{value * 100:+.{places}f}%"


def _bar(value: Decimal, maximum: Decimal, width: int = 220) -> int:
    if maximum <= 0:
        return 0
    ratio = min(abs(value) / maximum, Decimal(1))
    return int(ratio * width)


def render(
    snapshot: Snapshot,
    plan: Plan | None,
    top: Iterable[Market],
    tests: int = 0,
    suite: str = "",
) -> str:
    rows = list(top)[:5]
    max_apr = max((abs(funding_view(m).apr_percent) for m in rows), default=Decimal(1))
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="ui-monospace, SFMono-Regular, Menlo, monospace">'
    )
    parts.append(
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="#0b0f14"/><stop offset="1" stop-color="#101a26"/></linearGradient></defs>'
    )
    parts.append(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
    parts.append(f'<rect x="0" y="0" width="{W}" height="6" fill="{BLUE}"/>')

    # header
    parts.append(f'<text x="48" y="72" fill="{INK}" font-size="30" font-weight="700">dreamDEX · Delta-Neutral Funding Harvester</text>')
    parts.append(
        f'<text x="48" y="102" fill="{MUTED}" font-size="15">Hideki testnet · chain 50383 · '
        f'block {snapshot.block:,} · {escape(snapshot.at)}</text>'
    )
    badge = "EXECUTABLE" if plan and plan.executable else "WATCH ONLY"
    color = GREEN if plan and plan.executable else AMBER
    parts.append(f'<rect x="1010" y="52" width="142" height="34" rx="17" fill="{color}" opacity="0.16"/>')
    parts.append(f'<text x="1081" y="75" fill="{color}" font-size="14" font-weight="700" text-anchor="middle">{badge}</text>')

    # left panel: funding board
    parts.append(f'<rect x="40" y="130" width="520" height="330" rx="14" fill="{PANEL}" stroke="#1e2a38"/>')
    parts.append(f'<text x="64" y="166" fill="{MUTED}" font-size="13" letter-spacing="2">FUNDING BOARD · APR</text>')
    y = 200
    for m in rows:
        apr = funding_view(m).apr_percent
        c = GREEN if apr >= 0 else RED
        parts.append(f'<text x="64" y="{y}" fill="{INK}" font-size="16">{escape(m.display):<14}</text>')
        parts.append(f'<text x="200" y="{y}" fill="{c}" font-size="16" font-weight="700">{apr:+.1f}%</text>')
        parts.append(f'<rect x="300" y="{y - 11}" width="220" height="8" rx="4" fill="#1b2531"/>')
        parts.append(f'<rect x="300" y="{y - 11}" width="{_bar(apr, max_apr)}" height="8" rx="4" fill="{c}"/>')
        y += 48

    # right panel: plan
    parts.append(f'<rect x="584" y="130" width="576" height="330" rx="14" fill="{PANEL}" stroke="#1e2a38"/>')
    if plan:
        parts.append(f'<text x="608" y="166" fill="{MUTED}" font-size="13" letter-spacing="2">CASH-AND-CARRY PLAN</text>')
        parts.append(f'<text x="608" y="206" fill="{INK}" font-size="24" font-weight="700">{escape(plan.pair.name)}</text>')
        parts.append(f'<text x="608" y="234" fill="{MUTED}" font-size="14">short {escape(plan.pair.perp.display)} · long {escape(plan.pair.spot.display)}</text>')
        cells = [
            ("notional", f"${_fmt(plan.notional)}"),
            ("funding APR", f"{plan.apr_percent:+.1f}%"),
            ("carry / day", f"${_fmt(plan.daily_carry, 3)}"),
            ("spot qty", _fmt(plan.spot_qty, 6)),
            ("perp qty", _fmt(plan.perp_qty, 6)),
            ("margin", f"${_fmt(plan.perp_margin)}"),
            ("liq price", f"${_fmt(plan.liq_price or Decimal(0))}"),
            ("liq buffer", _pct(plan.liq_buffer)),
        ]
        for idx, (label, value) in enumerate(cells):
            col, row = idx % 2, idx // 2
            x = 608 + col * 268
            yy = 276 + row * 44
            parts.append(f'<text x="{x}" y="{yy}" fill="{MUTED}" font-size="12">{label}</text>')
            tone = INK
            if label == "liq buffer":
                tone = GREEN if plan.liq_buffer >= Decimal("0.15") else AMBER
            parts.append(f'<text x="{x}" y="{yy + 19}" fill="{tone}" font-size="17" font-weight="600">{escape(value)}</text>')
        if plan.reasons:
            parts.append(f'<text x="608" y="448" fill="{AMBER}" font-size="12">{escape(plan.reasons[0][:76])}</text>')
    else:
        parts.append(f'<text x="608" y="166" fill="{MUTED}" font-size="13" letter-spacing="2">NO PLAN</text>')
        parts.append(f'<text x="608" y="210" fill="{INK}" font-size="20">No perp/spot pair on this venue</text>')

    # footer
    parts.append(f'<line x1="40" y1="492" x2="1160" y2="492" stroke="#1e2a38"/>')
    parts.append(f'<text x="40" y="526" fill="{MUTED}" font-size="13">source  indexer hideki-dev.smk.somnia.host · {len(snapshot.perps)} perp markets · {len(snapshot.spots)} spot markets</text>')
    parts.append(f'<text x="40" y="552" fill="{MUTED}" font-size="13">safety  getMaxPerpOrderSize precheck · revert 0xd48c4403 benign · 0x3a23d825 halts sizing</text>')
    if tests:
        parts.append(f'<text x="40" y="600" fill="{GREEN}" font-size="18" font-weight="700">{tests}/{tests} tests green</text>')
        parts.append(f'<text x="220" y="600" fill="{MUTED}" font-size="14">{escape(suite or "verify.py")}</text>')
    parts.append(f'<text x="1160" y="600" fill="{MUTED}" font-size="13" text-anchor="end">github.com/9xj89gzrtw-hue/dreamdex-perps-agent</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def write(snapshot: Snapshot, plan: Plan | None, top: Iterable, path: str, tests: int = 0, suite: str = "") -> str:
    svg = render(snapshot, plan, top, tests=tests, suite=suite)
    with open(path, "w") as fh:
        fh.write(svg)
    return svg
