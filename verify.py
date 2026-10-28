#!/usr/bin/env python3
"""One command that proves the agent works.

    python3 verify.py              # tests + fixture + live read + card
    python3 verify.py --offline    # tests + fixture only, no network
    python3 verify.py --account 0x...   # also ask the SDK for a real max size

Exit code is 0 only when the unit suite passes and, when online, the live
indexer answers. A precheck that says "not placeable" is reported but does not
fail the run: an unfunded testnet account is the expected state.
"""

from __future__ import annotations

import argparse
import sys
import time
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agent import card as card_mod  # noqa: E402
from agent.funding import funding_view  # noqa: E402
from agent.indexer import fetch_snapshot, load_fixture  # noqa: E402
from agent.precheck import precheck  # noqa: E402
from agent.strategy import best, rank  # noqa: E402

FIXTURE = ROOT / "fixtures" / "markets_latest.json"
GREEN, RED, AMBER, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def ok(label: str, detail: str = "") -> None:
    print(f"  {GREEN}ok{RESET}    {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def bad(label: str, detail: str = "") -> None:
    print(f"  {RED}FAIL{RESET}  {label}" + (f"  {detail}" if detail else ""))


def warn(label: str, detail: str = "") -> None:
    print(f"  {AMBER}note{RESET}  {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def run_suite() -> tuple[bool, int, int]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"))
    total = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w")).run(suite)
    failures = len(result.failures) + len(result.errors)
    if failures:
        for case, trace in (result.failures + result.errors)[:3]:
            print(f"    {case}\n{trace}")
    return result.wasSuccessful(), total, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="verify the dreamDEX perps agent")
    ap.add_argument("--offline", action="store_true", help="skip every network call")
    ap.add_argument("--account", default=None, help="address for the SDK precheck")
    ap.add_argument("--capital", default="100")
    ap.add_argument("--leverage", default="3")
    ap.add_argument("--out", default=str(ROOT / "out" / "card.svg"))
    ap.add_argument("--png", default=str(ROOT / "out" / "card.png"))
    ap.add_argument("--market", default="BTC-PERP")
    args = ap.parse_args(argv)

    started = time.time()
    capital = Decimal(args.capital)
    leverage = Decimal(args.leverage)
    failed = False

    print(f"\n\033[1mdreamDEX perps agent · verify\033[0m  {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
    print(f"{DIM}chain 50383 · python {sys.version.split()[0]} · {ROOT}{RESET}\n")

    print("1. unit suite")
    passed, total, failures = run_suite()
    if passed:
        ok(f"{total}/{total} tests", f"{time.time() - started:.2f}s")
    else:
        failed = True
        bad(f"{failures} of {total} tests failed")

    print("\n2. captured fixture")
    try:
        snap = load_fixture(FIXTURE)
        assert len(snap.perps) == 21, f"expected 21 perps, got {len(snap.perps)}"
        assert len(snap.spots) == 3, f"expected 3 spots, got {len(snap.spots)}"
        assert snap.block > 0
        assert all(m.funding_window_sec == 28800 and m.funding_interval_sec == 3600 for m in snap.perps)
        ok("21 perps / 3 spots", f"block {snap.block:,} · {snap.at}")
    except Exception as exc:  # noqa: BLE001
        failed = True
        bad("fixture is unusable", str(exc))

    live = None
    if args.offline:
        print("\n3. live indexer\n  skipped (--offline)")
    else:
        print("\n3. live indexer")
        try:
            live = fetch_snapshot()
            assert live.perps, "indexer returned no perp markets"
            assert live.block > 0, "RPC did not return a block"
            ok(f"{len(live.perps)} perps / {len(live.spots)} spots", f"block {live.block:,} · {live.source}")
            top = max(live.perps, key=lambda m: abs(funding_view(m).apr_percent))
            ok(f"widest funding {top.display}", f"APR {funding_view(top).apr_percent:+.1f}%")
        except Exception as exc:  # noqa: BLE001
            failed = True
            bad("live read failed", f"{type(exc).__name__}: {exc}")

    snapshot = live or snap
    plans = rank(snapshot, capital, leverage)
    plan = plans[0] if plans else None
    print("\n4. plan")
    if plan is None:
        failed = True
        bad("no perp/spot pair to trade")
    else:
        state = f"{GREEN}executable{RESET}" if plan.executable else f"{AMBER}blocked: {plan.reasons[0]}{RESET}"
        ok(f"{plan.pair.name} {state}", f"APR {plan.apr_percent:+.2f}% · ${plan.daily_carry:.3f}/day · liq buffer {plan.liq_buffer * 100:.2f}%")

    if args.account and plan is not None:
        print("\n5. SDK precheck")
        market = snapshot.perp_by_display(args.market)
        result = precheck(market, args.account, market.mark, plan.perp_margin, leverage, False)
        if result.source != "sdk":
            warn("SDK bridge unavailable, analytic bound only", result.limited_by)
        elif result.ok:
            ok("venue accepts the size", f"max {result.max_quantity} · limitedBy {result.limited_by}")
        else:
            warn("venue says not placeable", f"max {result.max_quantity} · limitedBy {result.limited_by}")
    else:
        print("\n5. SDK precheck\n  skipped (pass --account to run it)")

    print("\n6. card")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    top = sorted(snapshot.perps, key=lambda m: -abs(m.funding_rate_raw))
    svg = card_mod.write(snapshot, plan, top, str(out), tests=total, suite="verify.py")
    ok(f"{out.relative_to(ROOT)}", f"{len(svg):,} bytes")
    png = Path(args.png)
    if card_mod_png(out, png):
        ok(f"{png.relative_to(ROOT)}", f"{png.stat().st_size:,} bytes")

    elapsed = time.time() - started
    print()
    if failed:
        print(f"{RED}verify failed{RESET} in {elapsed:.2f}s")
        return 1
    print(f"{GREEN}verify passed{RESET} in {elapsed:.2f}s · {total} tests · block {snapshot.block:,}")
    return 0


def card_mod_png(svg: Path, png: Path) -> bool:
    import subprocess

    try:
        from agent.cli import _svg_to_png

        return _svg_to_png(svg, png)
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())
