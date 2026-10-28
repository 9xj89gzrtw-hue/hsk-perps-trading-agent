#!/usr/bin/env python3
"""Capture one live Hideki snapshot into fixtures/ (for replay and tests)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.indexer import fetch_snapshot, save_fixture  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "fixtures" / "markets_latest.json"))
    args = ap.parse_args()
    snap = fetch_snapshot()
    path = save_fixture(snap, args.out)
    print(f"{snap.at} block={snap.block} perps={len(snap.perps)} spots={len(snap.spots)} -> {path}")
    for m in sorted(snap.perps, key=lambda x: -abs(x.funding_rate_raw))[:8]:
        print(f"  {m.display:12} mark={m.mark:>12.4f} funding={m.funding_rate_raw / 1e18:+.6f} "
              f"window={m.funding_window_sec}s interval={m.funding_interval_sec}s oi={m.open_interest:,.2f}")
    for m in snap.spots:
        print(f"  SPOT {m.display:12} last={m.last:>12.4f} min={m.min_qty:.8f} lot={m.lot:.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
