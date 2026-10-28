"""The SVG card: an artifact that survives after the terminal scrolls away."""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from agent import card
from agent.indexer import load_fixture
from agent.models import Market, Snapshot
from agent.strategy import best

D = Decimal


def fixture_snapshot() -> Snapshot:
    return load_fixture(Path(__file__).resolve().parents[1] / "fixtures" / "markets_latest.json")


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.snap = fixture_snapshot()
        self.plan = best(self.snap, D(100), D(3))
        self.top = sorted(self.snap.perps, key=lambda m: -abs(m.funding_rate_raw))
        self.svg = card.render(self.snap, self.plan, self.top, tests=61, suite="verify.py")

    def test_is_a_standalone_svg(self):
        self.assertTrue(self.svg.startswith("<svg"))
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', self.svg)
        self.assertTrue(self.svg.rstrip().endswith("</svg>"))

    def test_has_the_documented_size(self):
        self.assertIn(f'width="{card.W}" height="{card.H}"', self.svg)

    def test_shows_the_block_and_source(self):
        self.assertIn(f"block {self.snap.block:,}", self.svg)
        self.assertIn("indexer hideki-dev.smk.somnia.host", self.svg)

    def test_shows_the_chosen_pair(self):
        self.assertIn("BTC-PERP / WBTC/USDso", self.svg)
        self.assertIn("CASH-AND-CARRY PLAN", self.svg)

    def test_badge_says_executable(self):
        self.assertIn("EXECUTABLE", self.svg)
        self.assertNotIn("WATCH ONLY", self.svg)

    def test_badge_says_watch_only_without_a_plan(self):
        svg = card.render(self.snap, None, self.top)
        self.assertIn("WATCH ONLY", svg)
        self.assertIn("NO PLAN", svg)

    def test_counts_markets_from_the_snapshot(self):
        self.assertIn("21 perp markets", self.svg)
        self.assertIn("3 spot markets", self.svg)

    def test_prints_the_test_count_only_when_known(self):
        self.assertIn("61/61 tests green", self.svg)
        bare = card.render(self.snap, self.plan, self.top)
        self.assertNotIn("tests green", bare)

    def test_escapes_untrusted_symbol_names(self):
        evil = Market(id="0x1", base="<script>", quote="USDso", kind="PERP",
                      base_decimals=18, quote_decimals=18, pool="0x1", margin_bank="0x2",
                      mark_raw=10**18, funding_rate_raw=10**15,
                      funding_window_sec=28800, funding_interval_sec=3600)
        svg = card.render(Snapshot(at="t", block=1, perps=[evil]), None, [evil])
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_infinite_break_even_is_rendered(self):
        # A zero-fee, zero-carry plan would divide by zero; the formatter must not.
        self.assertEqual(card._fmt(D("Infinity")), "∞")

    def test_percent_format_is_signed(self):
        self.assertEqual(card._pct(D("0.1234")), "+12.34%")
        self.assertEqual(card._pct(D("-0.05")), "-5.00%")


class WriteTest(unittest.TestCase):
    def test_writes_the_file_and_returns_the_markup(self):
        snap = fixture_snapshot()
        plan = best(snap, D(100), D(3))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.svg"
            svg = card.write(snap, plan, snap.perps, str(path), tests=3)
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(), svg)


if __name__ == "__main__":
    unittest.main()
