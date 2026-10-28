"""Funding normalisation. The 8x error this file guards against is the whole point."""

from __future__ import annotations

import unittest
from decimal import Decimal

from agent.config import YEAR_SECONDS
from agent.funding import annualize, carry_usd, funding_view, per_interval_raw, view_of

from helpers import perp


class RateTest(unittest.TestCase):
    def test_rate_is_a_fraction_of_one(self):
        v = view_of(10**15, 28800, 3600)
        self.assertEqual(v.rate, Decimal("0.001"))

    def test_per_interval_truncates_like_the_contract(self):
        v = view_of(1436 * 10**12 + 7, 28800, 3600)
        self.assertEqual(v.per_interval_raw, (1436 * 10**12 + 7) // 8)

    def test_apr_uses_the_window_not_the_interval(self):
        v = view_of(10**15, 28800, 3600)
        self.assertEqual(v.apr, Decimal("0.001") * Decimal(YEAR_SECONDS) / Decimal(28800))
        self.assertEqual(v.apr_percent, Decimal("109.5"))

    def test_changing_interval_does_not_change_apr(self):
        hourly = view_of(10**15, 28800, 3600)
        four_hourly = view_of(10**15, 28800, 14400)
        self.assertEqual(hourly.apr, four_hourly.apr)
        self.assertNotEqual(hourly.per_interval, four_hourly.per_interval)

    def test_daily_compounds_intervals(self):
        v = view_of(10**15, 28800, 3600)
        self.assertEqual(v.daily, v.per_interval * Decimal(24))

    def test_unknown_without_window(self):
        v = view_of(10**15, 0, 3600)
        self.assertFalse(v.known)
        self.assertEqual(v.apr, Decimal(0))
        self.assertEqual(v.daily, Decimal(0))

    def test_unknown_with_zero_rate(self):
        self.assertFalse(view_of(0, 28800, 3600).known)

    def test_sides(self):
        pos = view_of(10**15, 28800, 3600)
        self.assertEqual(pos.paid_side, "short")
        self.assertEqual(pos.paying_side, "long")
        neg = view_of(-10**15, 28800, 3600)
        self.assertEqual(neg.paid_side, "long")
        self.assertEqual(neg.paying_side, "short")
        self.assertFalse(neg.positive)


class CarryTest(unittest.TestCase):
    def test_one_day_of_carry_on_notional(self):
        v = view_of(10**15, 28800, 3600)  # 0.125% per hour
        self.assertEqual(carry_usd(Decimal(1000), v, Decimal(24)), Decimal("3"))

    def test_one_hour_matches_per_interval(self):
        v = view_of(10**15, 28800, 3600)
        self.assertEqual(carry_usd(Decimal(100), v, Decimal(1)), Decimal("0.0125"))

    def test_unknown_view_pays_nothing(self):
        self.assertEqual(carry_usd(Decimal(100), view_of(0, 0, 0), Decimal(24)), Decimal(0))

    def test_negative_rate_gives_negative_carry(self):
        v = view_of(-10**15, 28800, 3600)
        self.assertLess(carry_usd(Decimal(100), v, Decimal(1)), Decimal(0))


class HelperTest(unittest.TestCase):
    def test_annualize_matches_view(self):
        self.assertEqual(annualize(10**15, 28800), view_of(10**15, 28800, 3600).apr)

    def test_annualize_guards_zero_window(self):
        self.assertEqual(annualize(10**15, 0), Decimal(0))

    def test_per_interval_raw_guards_zero(self):
        self.assertEqual(per_interval_raw(10**15, 0, 3600), 0)
        self.assertEqual(per_interval_raw(10**15, 28800, 0), 0)

    def test_funding_view_reads_a_market(self):
        m = perp(funding="0.002", window=28800, interval=3600)
        v = funding_view(m)
        self.assertEqual(v.per_interval, Decimal("0.00025"))
        self.assertEqual(v.rate, Decimal("0.002"))

    def test_live_snapshot_rates_are_eight_hour_windows(self):
        # Every market in the captured snapshot settles hourly off an 8h window.
        from agent.indexer import load_fixture

        snap = load_fixture("fixtures/markets_latest.json")
        for m in snap.perps:
            self.assertEqual(m.funding_window_sec, 28800, m.display)
            self.assertEqual(m.funding_interval_sec, 3600, m.display)
            self.assertEqual(m.funding_window_sec // m.funding_interval_sec, 8)


if __name__ == "__main__":
    unittest.main()
