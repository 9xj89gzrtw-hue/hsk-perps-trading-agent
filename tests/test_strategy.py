"""The cash-and-carry planner: capital, hedge ratio, blockers, ranking."""

from __future__ import annotations

import unittest
from decimal import Decimal

from agent.indexer import load_fixture
from agent.models import Snapshot
from agent.strategy import best, build_pairs, hedge_for, plan_pair, rank

from helpers import perp, spot

D = Decimal


class HedgeTest(unittest.TestCase):
    def test_btc_maps_to_wbtc(self):
        self.assertEqual(hedge_for(perp(base="BTC"), [spot(base="WBTC")]).base, "WBTC")

    def test_eth_maps_to_weth(self):
        self.assertEqual(hedge_for(perp(base="ETH"), [spot(base="WETH")]).base, "WETH")

    def test_unlisted_hedge_returns_none(self):
        self.assertIsNone(hedge_for(perp(base="HYPE"), [spot(base="WBTC"), spot(base="WETH")]))

    def test_build_pairs_only_for_listed_spots(self):
        pairs = build_pairs(
            [perp(base="BTC", pool="0xa"), perp(base="HYPE", pool="0xb")],
            [spot(base="WBTC", pool="0xc")],
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].perp.base, "BTC")


class PlanTest(unittest.TestCase):
    def pair(self, funding="0.001", perp_mark="80000", spot_price="80000"):
        return build_pairs([perp(funding=funding, mark=perp_mark)], [spot(price=spot_price)])[0]

    def test_positive_funding_is_executable(self):
        plan = plan_pair(self.pair(), D(100), D(3))
        self.assertTrue(plan.executable, plan.reasons)
        self.assertAlmostEqual(float(plan.apr_percent), 109.5, places=1)

    def test_negative_funding_is_blocked(self):
        plan = plan_pair(self.pair(funding="-0.001"), D(100), D(3))
        self.assertFalse(plan.executable)
        self.assertTrue(any("spot short" in r for r in plan.reasons))

    def test_low_apr_is_blocked_by_the_floor(self):
        plan = plan_pair(self.pair(funding="0.00001"), D(100), D(3), min_apr=D("0.5"))
        self.assertFalse(plan.executable)
        self.assertTrue(any("floor" in r for r in plan.reasons))

    def test_capital_is_split_between_spot_and_margin(self):
        plan = plan_pair(self.pair(), D(100), D(3))
        used = plan.spot_cost + plan.perp_margin
        self.assertLessEqual(used, D(100))
        self.assertGreater(used, D(90))  # lot rounding leaves a little idle

    def test_legs_are_delta_neutral_within_one_lot(self):
        plan = plan_pair(self.pair(), D(100), D(3))
        diff = abs(plan.perp_qty * plan.pair.perp.mark - plan.spot_qty * plan.pair.spot.mark)
        self.assertLess(diff, plan.pair.perp.mark * plan.pair.perp.lot * 2)

    def test_short_has_a_liquidation_buffer(self):
        plan = plan_pair(self.pair(), D(100), D(3))
        self.assertIsNotNone(plan.liq_price)
        self.assertGreater(plan.liq_price, plan.pair.perp.mark)
        self.assertGreater(plan.liq_buffer, Decimal("0.1"))

    def test_break_even_is_fees_over_hourly_carry(self):
        plan = plan_pair(self.pair(), D(100), D(3))
        hourly = plan.daily_carry / D(24)
        self.assertAlmostEqual(float(plan.breakeven_hours), float(plan.fee_cost / hourly), places=6)

    def test_notes_explain_the_window_normalisation(self):
        plan = plan_pair(self.pair(), D(100), D(3))
        self.assertTrue(any("8x normalisation" in n for n in plan.notes))

    def test_basis_is_reported_from_the_index(self):
        plan = plan_pair(self.pair(perp_mark="80100"), D(100), D(3))
        # index defaults to 80010, so a mark of 80100 trades rich
        self.assertGreater(plan.basis, 0)

    def test_zero_mark_is_blocked(self):
        plan = plan_pair(build_pairs([perp(mark="0")], [spot()])[0], D(100), D(3))
        self.assertFalse(plan.executable)
        self.assertIn("no mark price", plan.reasons)

    def test_min_quantity_blocks_a_tiny_account(self):
        plan = plan_pair(self.pair(), D(0.01), D(3), min_liq_buffer=D(0))
        self.assertFalse(plan.executable)
        self.assertTrue(any("below the venue grid" in r for r in plan.reasons))


class RankTest(unittest.TestCase):
    def test_rank_puts_executable_first(self):
        snap = Snapshot(
            at="t", block=1,
            perps=[perp(base="ETH", funding="-0.001", pool="0xe"), perp(base="BTC", funding="0.001", pool="0xb")],
            spots=[spot(base="WBTC", pool="0xw"), spot(base="WETH", pool="0xwe")],
        )
        plans = rank(snap, D(100), D(3))
        self.assertEqual(plans[0].pair.perp.base, "BTC")
        self.assertTrue(plans[0].executable)
        self.assertFalse(plans[1].executable)

    def test_best_on_empty_snapshot(self):
        self.assertIsNone(best(Snapshot(at="t", block=1), D(100)))

    def test_best_on_fixture_is_the_btc_carry(self):
        snap = load_fixture("fixtures/markets_latest.json")
        plan = best(snap, D(100), D(3))
        self.assertIsNotNone(plan)
        self.assertEqual(plan.pair.perp.base, "BTC")
        self.assertTrue(plan.executable, plan.reasons)
        self.assertGreater(plan.apr_percent, D(100))
        self.assertLess(plan.apr_percent, D(200))


if __name__ == "__main__":
    unittest.main()
