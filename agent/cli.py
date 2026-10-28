"""Command line: scan, plan, precheck, card, run.

Every command works offline against `--fixture` and live against the indexer.
Nothing writes unless `--execute --yes` is passed, and even then it goes through
the SDK bridge, never through hand-rolled calldata.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from . import card as card_mod
from . import terminal as term
from .config import CHAIN_ID, INDEXER_URL, NETWORK
from .funding import funding_view
from .indexer import IndexerError, fetch_snapshot, load_fixture
from .models import Snapshot
from .precheck import decode_revert, precheck
from .strategy import Plan, best, build_pairs, rank

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
DEFAULT_FIXTURE = ROOT / "fixtures" / "markets_latest.json"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(args: argparse.Namespace) -> Snapshot:
    path = getattr(args, "fixture_path", None)
    if path:
        return load_fixture(path)
    if getattr(args, "fixture", False):
        return load_fixture(DEFAULT_FIXTURE)
    return fetch_snapshot()


def cmd_scan(args: argparse.Namespace) -> int:
    snap = load(args)
    if args.json:
        print(json.dumps(
            {
                "at": snap.at,
                "block": snap.block,
                "perps": [
                    {
                        "market": m.display,
                        "mark": str(m.mark),
                        "index": str(m.index),
                        "fundingRate": str(m.funding_rate_raw),
                        "apr": str(funding_view(m).apr),
                        "windowSec": m.funding_window_sec,
                        "intervalSec": m.funding_interval_sec,
                        "openInterest": str(m.open_interest),
                        "basisBps": str(m.basis * 10000),
                    }
                    for m in snap.perps
                ],
            },
            indent=1,
        ))
        return 0
    print(term.header(snap))
    print()
    print(term.funding_table(snap, limit=args.limit))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    snap = load(args)
    capital = Decimal(str(args.capital))
    plans = rank(snap, capital, Decimal(str(args.leverage)))
    if args.json:
        print(json.dumps([_plan_json(p) for p in plans], indent=1))
        return 0
    print(term.header(snap))
    print()
    print(term.pairs_table(plans))
    print()
    if plans:
        print(term.plan_block(plans[0]))
    return 0


def _plan_json(p: Plan) -> dict:
    return {
        "pair": p.pair.name,
        "executable": p.executable,
        "notional": str(p.notional),
        "perpQty": str(p.perp_qty),
        "spotQty": str(p.spot_qty),
        "margin": str(p.perp_margin),
        "apr": str(p.funding.apr),
        "dailyCarry": str(p.daily_carry),
        "feeCost": str(p.fee_cost),
        "breakevenHours": str(p.breakeven_hours),
        "liqPrice": str(p.liq_price or 0),
        "liqBuffer": str(p.liq_buffer),
        "basis": str(p.basis),
        "score": str(p.score),
        "reasons": list(p.reasons),
    }


def cmd_check(args: argparse.Namespace) -> int:
    snap = load(args)
    market = snap.perp_by_display(args.market) if args.market else (snap.perps[0] if snap.perps else None)
    if market is None:
        print(f"market {args.market!r} not found", file=sys.stderr)
        return 2
    price = Decimal(str(args.price)) if args.price else market.mark * (Decimal(1) + Decimal(str(args.slippage_bps)) / Decimal(10_000))
    result = precheck(market, args.account, price, Decimal(str(args.margin)), Decimal(str(args.leverage)), not args.short)
    print(f"{market.display} price {price} account {args.account}")
    print(json.dumps(result.as_dict(), indent=1))
    if result.source == "analytic":
        print("hint: npm --prefix bridge install && node bridge/precheck.mjs ... for the venue's own number", file=sys.stderr)
    return 0 if result.ok else 1


def cmd_card(args: argparse.Namespace) -> int:
    snap = load(args)
    plans = rank(snap, Decimal(str(args.capital)), Decimal(str(args.leverage)))
    plan = plans[0] if plans else None
    top = sorted(snap.perps, key=lambda m: -abs(m.funding_rate_raw))
    tests = args.tests
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    svg = card_mod.write(snap, plan, top, str(out), tests=tests, suite=args.suite)
    print(f"wrote {out} ({len(svg)} bytes)")
    if args.png:
        png = Path(args.png)
        if _svg_to_png(out, png):
            print(f"wrote {png}")
        else:
            print("png conversion unavailable (install rsvg-convert or use the SVG)")
    return 0


def _svg_to_png(svg: Path, png: Path) -> bool:
    import shutil

    for cmd in (["rsvg-convert", "-o", str(png), str(svg)], ["magick", str(svg), str(png)]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                return png.exists()
            except Exception:  # noqa: BLE001
                continue
    # macOS: qlmanage renders SVG thumbnails at the requested size.
    if shutil.which("qlmanage"):
        try:
            subprocess.run(
                ["qlmanage", "-t", "-s", "1200", "-o", str(png.parent), str(svg)],
                check=True,
                capture_output=True,
            )
            produced = png.parent / (svg.name + ".png")
            if produced.exists():
                produced.replace(png)
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def cmd_run(args: argparse.Namespace) -> int:
    snap = load(args)
    capital = Decimal(str(args.capital))
    leverage = Decimal(str(args.leverage))
    plans = rank(snap, capital, leverage)
    plan = plans[0] if plans else None
    print(term.header(snap))
    print()
    print(term.pairs_table(plans))
    print()
    if plan is None:
        print("no perp/spot pair available")
        return 1
    print(term.plan_block(plan))
    record = {
        "at": now(),
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "block": snap.block,
        "capital": str(capital),
        "leverage": str(leverage),
        "plan": _plan_json(plan),
        "executed": False,
    }
    if not plan.executable and not args.force:
        print(term.paint("plan is not executable; nothing sent", term.AMBER))
        _journal(record)
        return 1
    if plan.pair.perp.pool == "":
        print("market row has no pool address; refusing to send")
        _journal(record)
        return 1
    price = plan.pair.perp.mark
    result = precheck(plan.pair.perp, args.account or _default_account(), price, plan.perp_margin, leverage, False)
    print()
    print(f"precheck  source={result.source} placeable={result.placeable} max={result.max_quantity} limitedBy={result.limited_by}")
    record["precheck"] = result.as_dict()
    if not result.ok:
        print(term.paint("precheck says no; refusing to send", term.AMBER))
        _journal(record)
        return 1
    if not args.execute:
        print(term.paint("[dry-run] would place the perp leg; pass --execute --yes to send", term.DIM))
        _journal(record)
        return 0
    if not args.yes:
        print("--execute requires --yes (testnet orders are still real transactions)", file=sys.stderr)
        _journal(record)
        return 2
    if result.max_quantity < plan.perp_qty:
        print(f"plan qty {plan.perp_qty} above venue max {result.max_quantity}; refusing", file=sys.stderr)
        _journal(record)
        return 1
    sent = _place(plan, args)
    record["executed"] = sent
    _journal(record)
    return 0 if sent.get("ok") else 3


def _default_account() -> str:
    key = os.environ.get("PRIVATE_KEY")
    if key:
        try:
            from eth_account import Account

            return Account.from_key(key).address
        except Exception:  # noqa: BLE001
            pass
    return "0x0000000000000000000000000000000000000000"


def _place(plan: Plan, args: argparse.Namespace) -> dict:
    script = ROOT / "bridge" / "place.mjs"
    if not script.exists() or not (ROOT / "bridge" / "node_modules").exists():
        print("bridge not installed: npm --prefix bridge install", file=sys.stderr)
        return {"ok": False, "error": "bridge-missing"}
    price_raw = int(plan.pair.perp.mark * (Decimal(10) ** 18))
    qty_raw = int(plan.perp_qty * (Decimal(10) ** plan.pair.perp.base_decimals))
    cmd = [
        "node",
        str(script),
        "--pool",
        plan.pair.perp.pool,
        "--is-bid",
        "false",
        "--price",
        str(price_raw),
        "--quantity",
        str(qty_raw),
        "--type",
        args.order_type,
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        revert = decode_revert(proc.stderr + proc.stdout)
        if revert:
            print(f"revert {revert.name} ({revert.selector}): {revert.disposition}")
        return {"ok": False, "error": (proc.stderr or proc.stdout)[-500:]}
    line = next((ln for ln in reversed(proc.stdout.strip().splitlines()) if ln.startswith("{")), "{}")
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        payload = {"raw": proc.stdout[-500:]}
    payload["ok"] = proc.returncode == 0
    return payload


def _journal(record: dict) -> None:
    OUT.mkdir(exist_ok=True)
    with (OUT / "runlog.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    (OUT / "last-run.json").write_text(json.dumps(record, indent=1))


def cmd_pairs(args: argparse.Namespace) -> int:
    snap = load(args)
    for pair in build_pairs(snap.perps, snap.spots):
        print(f"{pair.name:34} perp {pair.perp.pool}  spot {pair.spot.pool}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="agent", description="dreamDEX delta-neutral funding harvester")
    ap.add_argument("--fixture-path", help="read a captured snapshot instead of the live indexer")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--capital", default="100", help="USDso of total capital (spot + margin)")
        p.add_argument("--leverage", default="3", help="perp margin leverage, capped by the market's imr")
        p.add_argument("--fixture", action="store_true", help="use fixtures/markets_latest.json")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("scan")
    common(p)
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("plan")
    common(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("pairs")
    common(p)
    p.set_defaults(func=cmd_pairs)

    p = sub.add_parser("check")
    common(p)
    p.add_argument("--market", default="BTC-PERP")
    p.add_argument("--account", required=True)
    p.add_argument("--price", default=None)
    p.add_argument("--margin", default="20")
    p.add_argument("--slippage-bps", default="10")
    p.add_argument("--short", action="store_true", help="size a sell/bid=false order")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("card")
    common(p)
    p.add_argument("--out", default=str(OUT / "card.svg"))
    p.add_argument("--png", default=None)
    p.add_argument("--tests", type=int, default=0)
    p.add_argument("--suite", default="")
    p.set_defaults(func=cmd_card)

    p = sub.add_parser("run")
    common(p)
    p.add_argument("--account", default=None)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--order-type", default="IOC", choices=["IOC", "LIMIT", "FOK", "POST_ONLY"])
    p.set_defaults(func=cmd_run)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except IndexerError as exc:
        print(f"indexer: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
