"""Parsing and unit conversion: the layer every other number depends on."""

from __future__ import annotations

import unittest
from decimal import Decimal

from agent.models import Book, BookLevel, Market, PoolParams, Snapshot, dec, human, i

from helpers import perp, spot


class ParseTest(unittest.TestCase):
    def test_dec_keeps_full_precision(self):
        self.assertEqual(dec("0.000000000000000001"), Decimal("1e-18"))

    def test_dec_none_uses_default(self):
        self.assertEqual(dec(None), Decimal(0))
        self.assertEqual(dec(None, "2.5"), Decimal("2.5"))

    def test_dec_garbage_uses_default(self):
        self.assertEqual(dec("not-a-number"), Decimal(0))

    def test_i_parses_strings(self):
        self.assertEqual(i("42"), 42)
        self.assertEqual(i("0x10", 0), 0)

    def test_i_none_uses_default(self):
        self.assertEqual(i(None, 7), 7)
        self.assertEqual(i(None), 0)

    def test_human_18_decimals(self):
        self.assertEqual(human(10**18, 18), Decimal(1))

    def test_human_zero_decimals_is_identity(self):
        self.assertEqual(human(123, 0), Decimal(123))


class PoolParamsTest(unittest.TestCase):
    def test_margin_ratios_are_bps(self):
        p = PoolParams(pool="0xpool", initial_margin_bps=2000, maintenance_margin_bps=1000)
        self.assertEqual(p.imr, Decimal("0.2"))
        self.assertEqual(p.mmr, Decimal("0.1"))

    def test_fee_scale_matches_the_venue(self):
        # 100_000 = 100 bps = 1%: the number mainnet reports as its builder cap.
        p = PoolParams(pool="0xpool", taker_fee_bps_times_1k=100_000)
        self.assertEqual(p.taker_fee, Decimal("0.01"))
        p = PoolParams(pool="0xpool", maker_fee_bps_times_1k=1_000)
        self.assertEqual(p.maker_fee, Decimal("0.0001"))

    def test_zero_fee_stays_zero(self):
        p = PoolParams(pool="0xpool")
        self.assertEqual(p.taker_fee, Decimal(0))
        self.assertEqual(p.maker_fee, Decimal(0))


class MarketTest(unittest.TestCase):
    def test_mark_falls_back_to_last_price(self):
        m = Market(id="0x1", base="BTC", quote="USDso", kind="PERP", base_decimals=8,
                   quote_decimals=18, pool="0x1", margin_bank="0x2", mark_raw=0, last_raw=5 * 10**18)
        self.assertEqual(m.mark, Decimal(5))

    def test_basis_is_relative_to_index(self):
        m = perp(mark="101", index="100")
        self.assertEqual(m.basis, Decimal("0.01"))

    def test_basis_is_zero_without_index(self):
        m = perp(mark="101", index="0")
        self.assertEqual(m.basis, Decimal(0))

    def test_display_names_perp_and_spot(self):
        self.assertEqual(perp(base="BTC").display, "BTC-PERP")
        self.assertEqual(spot(base="WBTC").display, "WBTC/USDso")

    def test_open_interest_uses_base_decimals(self):
        m = perp(base="TAO", base_decimals=9)
        # helper writes 1000 * 10**9 raw units
        self.assertEqual(m.open_interest, Decimal(1000))

    def test_imr_falls_back_when_params_missing(self):
        m = Market(id="0x1", base="BTC", quote="USDso", kind="PERP", base_decimals=8,
                   quote_decimals=18, pool="0x1", margin_bank="0x2", initial_margin_bps=2500)
        self.assertEqual(m.imr, Decimal("0.25"))
        self.assertEqual(m.mmr, Decimal("0.125"))

    def test_imr_defaults_to_ten_percent(self):
        m = Market(id="0x1", base="BTC", quote="USDso", kind="PERP", base_decimals=8,
                   quote_decimals=18, pool="0x1", margin_bank="0x2")
        self.assertEqual(m.imr, Decimal("0.1"))

    def test_from_row_reads_the_indexer_shape(self):
        row = {
            "id": "0xabc", "baseSymbol": "ETH", "quoteSymbol": "USDso", "marketType": "PERP",
            "baseDecimals": 18, "quoteDecimals": 18, "markPrice": str(2000 * 10**18),
            "indexPrice": str(1999 * 10**18), "fundingRate": str(10**15),
            "fundingWindowSec": 28800, "fundingIntervalSec": 3600,
            "openInterest": str(5 * 10**18), "tickSize": str(10**16), "lotSize": str(10**15),
            "minQuantity": str(10**16), "perpStatus": None,
        }
        m = Market.from_row(row)
        self.assertEqual(m.base, "ETH")
        self.assertEqual(m.mark, Decimal(2000))
        self.assertEqual(m.imr, Decimal("0.1"))
        self.assertIsNone(m.perp_status)

    def test_from_row_tolerates_missing_columns(self):
        m = Market.from_row({"baseSymbol": "X"})
        self.assertEqual(m.kind, "SPOT")
        self.assertEqual(m.base_decimals, 18)
        self.assertEqual(m.mark_raw, 0)


class BookTest(unittest.TestCase):
    def test_mid_and_spread(self):
        b = Book("0x1", bids=(BookLevel(Decimal("99"), Decimal(1)),),
                 asks=(BookLevel(Decimal("101"), Decimal(1)),))
        self.assertEqual(b.mid, Decimal(100))
        self.assertEqual(b.spread_bps, Decimal(200))

    def test_empty_book_has_no_mid(self):
        b = Book("0x1")
        self.assertIsNone(b.mid)
        self.assertIsNone(b.spread_bps)

    def test_depth_notional_takes_top_levels(self):
        b = Book("0x1", bids=(BookLevel(Decimal(100), Decimal(2)),
                              BookLevel(Decimal(99), Decimal(1))))
        self.assertEqual(b.depth_notional("bid", 1), Decimal(200))
        self.assertEqual(b.depth_notional("bid", 10), Decimal(299))


class SnapshotTest(unittest.TestCase):
    def test_lookup_by_base_and_display(self):
        snap = Snapshot(at="t", block=1, perps=[perp(base="BTC")], spots=[spot(base="WBTC")])
        self.assertIsNotNone(snap.by_base("btc"))
        self.assertEqual(snap.perp_by_display("BTC-PERP").base, "BTC")
        self.assertIsNone(snap.by_base("BTC", "SPOT"))

    def test_missing_market_returns_none(self):
        snap = Snapshot(at="t", block=1)
        self.assertIsNone(snap.by_base("NOPE"))
        self.assertIsNone(snap.perp_by_display("NOPE-PERP"))


if __name__ == "__main__":
    unittest.main()
