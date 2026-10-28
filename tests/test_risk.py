"""Liquidation, margin health and sizing on the venue's grid."""

from __future__ import annotations

import unittest
from decimal import Decimal

from agent.risk import (
    align_down,
    align_price,
    align_up,
    health,
    liq_buffer,
    liquidation_price,
    maintenance_margin,
    margin_ratio,
    max_notional,
    pnl,
    size_order,
    top_up_needed,
)

from helpers import perp

D = Decimal


class PnlTest(unittest.TestCase):
    def test_long_profits_when_price_rises(self):
        self.assertEqual(pnl("long", D(100), D(110), D(2)), D(20))

    def test_short_profits_when_price_falls(self):
        self.assertEqual(pnl("short", D(100), D(90), D(2)), D(20))

    def test_short_loses_when_price_rises(self):
        self.assertEqual(pnl("short", D(100), D(110), D(2)), D(-20))

    def test_zero_quantity_is_zero(self):
        self.assertEqual(pnl("long", D(100), D(110), D(0)), D(0))


class MarginTest(unittest.TestCase):
    def test_maintenance_margin(self):
        self.assertEqual(maintenance_margin(D(100), D(2), D("0.05")), D("10"))

    def test_ratio_is_infinite_without_maintenance(self):
        self.assertEqual(margin_ratio(D(5), D(0)), D("Infinity"))

    def test_ratio_below_one_is_liquidatable(self):
        self.assertLess(margin_ratio(D(4), D(5)), 1)

    def test_max_notional_is_equity_over_imr(self):
        self.assertEqual(max_notional(D(100), D("0.2")), D(500))

    def test_max_notional_guards_zero_imr(self):
        self.assertEqual(max_notional(D(100), D(0)), D(0))


class LiquidationTest(unittest.TestCase):
    def test_short_liquidates_above_entry(self):
        liq = liquidation_price("short", D(100), D(10), D(100), D("0.05"))
        # (100 + 10) / 1.05
        self.assertEqual(liq, D(110) / D("1.05"))
        self.assertGreater(liq, 100)

    def test_long_liquidates_below_entry(self):
        liq = liquidation_price("long", D(100), D(10), D(100), D("0.05"))
        # (100 - 10) / 0.95
        self.assertEqual(liq, D(90) / D("0.95"))
        self.assertLess(liq, 100)

    def test_long_with_margin_above_entry_liquidates_at_zero(self):
        self.assertEqual(liquidation_price("long", D(100), D(10), D(2000), D("0.05")), D(0))

    def test_no_quantity_has_no_liquidation(self):
        self.assertIsNone(liquidation_price("short", D(100), D(0), D(10), D("0.05")))

    def test_short_buffer_is_distance_over_entry(self):
        self.assertEqual(liq_buffer("short", D(100), D(110)), D("0.1"))

    def test_long_buffer_is_distance_over_entry(self):
        self.assertEqual(liq_buffer("long", D(100), D(90)), D("0.1"))

    def test_buffer_without_price_is_zero(self):
        self.assertEqual(liq_buffer("short", D(100), None), D(0))


class GridTest(unittest.TestCase):
    def test_align_down_and_up(self):
        self.assertEqual(align_down(D("1.29"), D("0.1")), D("1.2"))
        self.assertEqual(align_up(D("1.21"), D("0.1")), D("1.3"))

    def test_zero_step_is_identity(self):
        self.assertEqual(align_down(D("1.23"), D(0)), D("1.23"))
        self.assertEqual(align_up(D("1.23"), D(0)), D("1.23"))

    def test_price_alignment_rounds_down(self):
        self.assertEqual(align_price(D("80000.987"), D("0.01")), D("80000.98"))


class SizingTest(unittest.TestCase):
    def test_sizes_from_margin_and_leverage(self):
        s = size_order(D(25), D(80000), perp(), D(3))
        self.assertTrue(s.ok)
        self.assertEqual(s.qty, D("0.00093"))  # 75 / 80000, aligned to the lot
        self.assertEqual(s.notional, D("74.4"))
        self.assertEqual(s.limited_by, "leverage")

    def test_leverage_cannot_exceed_the_market_imr(self):
        s = size_order(D(25), D(80000), perp(im_bps=500), D(100))
        self.assertEqual(s.limited_by, "initialMargin")
        self.assertEqual(s.qty, D("0.00625"))  # 25 / 0.05 / 80000

    def test_position_cap_wins(self):
        s = size_order(D(25), D(80000), perp(), D(3), position_cap_qty=D("0.0005"))
        self.assertEqual(s.limited_by, "maxPositionSize")
        self.assertEqual(s.qty, D("0.0005"))

    def test_below_min_quantity_is_rejected(self):
        s = size_order(D(1), D(80000), perp(), D(3))
        self.assertFalse(s.ok)
        self.assertEqual(s.limited_by, "minQuantity")
        self.assertEqual(s.qty, D(0))

    def test_quantity_aligns_down_to_lot(self):
        s = size_order(D(25), D(80000), perp(lot="0.001"), D(3))
        self.assertEqual(s.qty, D("0.000"))  # 0.00075 truncates to zero lots

    def test_slippage_shrinks_notional(self):
        s = size_order(D(25), D(80000), perp(), D(3), slippage_bps=D(100))
        self.assertLess(s.notional, D("75"))
        self.assertEqual(s.qty, D("0.00092"))

    def test_zero_price_is_rejected(self):
        s = size_order(D(25), D(0), perp(), D(3))
        self.assertFalse(s.ok)
        self.assertEqual(s.limited_by, "no-margin")


class HealthTest(unittest.TestCase):
    def test_health_ratio(self):
        self.assertEqual(health(D(100), D(1000), D(1), D("0.05")), D(2))

    def test_top_up_to_target_ratio(self):
        # need 0.05 * 1000 * 2 = 100; have 60 -> add 40
        self.assertEqual(top_up_needed(D(60), D(1000), D(1), D("0.05"), D(2)), D(40))

    def test_top_up_never_negative(self):
        self.assertEqual(top_up_needed(D(500), D(1000), D(1), D("0.05"), D(2)), D(0))


if __name__ == "__main__":
    unittest.main()
