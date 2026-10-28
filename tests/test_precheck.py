"""Revert decoding and the two-tier precheck (SDK first, analytic labelled)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from agent import precheck as pc
from agent.config import ERRORS
from agent.models import Market
from agent.precheck import Precheck, analytic, decode_revert

from helpers import perp

D = Decimal


class DecodeTest(unittest.TestCase):
    def test_ioc_no_fill_selector(self):
        r = decode_revert("execution reverted: 0xd48c4403")
        self.assertEqual(r.name, "ImmediateOrCancelNoFill")
        self.assertTrue(r.benign)
        self.assertIn("Benign", r.disposition)

    def test_insufficient_collateral_selector(self):
        r = decode_revert("0x3a23d825")
        self.assertEqual(r.name, "InsufficientCollateral")
        self.assertFalse(r.benign)
        self.assertIn("Never just retry", r.disposition)

    def test_native_payout_selector(self):
        r = decode_revert("reverted with 0x782b2567")
        self.assertEqual(r.name, "InsufficientGasForPayout")

    def test_decodes_by_name(self):
        r = decode_revert("Error: ImmediateOrCancelNoFill")
        self.assertEqual(r.selector, "0xd48c4403")

    def test_unknown_selector_is_kept_for_a_report(self):
        r = decode_revert("execution reverted: 0xdeadbeef")
        self.assertEqual(r.name, "UnknownRevert")
        self.assertEqual(r.selector, "0xdeadbeef")
        self.assertIn("feedback", r.disposition)

    def test_plain_text_decodes_to_none(self):
        self.assertIsNone(decode_revert("connection reset by peer"))
        self.assertIsNone(decode_revert(""))

    def test_every_known_selector_round_trips(self):
        for selector, (name, _) in ERRORS.items():
            self.assertEqual(decode_revert(selector).name, name)
            self.assertEqual(decode_revert(name).selector, selector)


class AnalyticTest(unittest.TestCase):
    def test_sizes_against_the_market(self):
        r = analytic(perp(), D(25), D(80000), D(3), is_bid=False)
        self.assertTrue(r.ok)
        self.assertTrue(r.placeable)
        self.assertEqual(r.source, "analytic")
        self.assertEqual(r.max_quantity, D("0.00093"))
        self.assertEqual(r.max_quantity_raw, 93_000)

    def test_stale_mark_is_not_priceable(self):
        r = analytic(perp(), D(25), D(0), D(3), is_bid=True)
        self.assertFalse(r.ok)
        self.assertFalse(r.priceable)
        self.assertEqual(r.limited_by, "stale-mark")

    def test_below_min_quantity_is_not_placeable(self):
        r = analytic(perp(), D(1), D(80000), D(3), is_bid=True)
        self.assertFalse(r.ok)
        self.assertEqual(r.limited_by, "minQuantity")

    def test_quantity_raw_uses_the_market_decimals(self):
        m = perp(base="TAO", base_decimals=9, mark="250")
        r = analytic(m, D(25), D(250), D(3), is_bid=False)
        self.assertEqual(r.max_quantity_raw, int(r.max_quantity * 10**9))

    def test_note_says_it_is_a_bound(self):
        r = analytic(perp(), D(25), D(80000), D(3), is_bid=False)
        self.assertIn("conservative bound", r.note)

    def test_as_dict_is_json_ready(self):
        d = analytic(perp(), D(25), D(80000), D(3), is_bid=False).as_dict()
        self.assertEqual(
            set(d),
            {"ok", "priceable", "placeable", "maxQuantity", "maxQuantityHuman",
             "limitedBy", "source", "block", "note"},
        )
        self.assertIsInstance(d["maxQuantity"], str)


class SdkBridgeTest(unittest.TestCase):
    def test_missing_bridge_returns_none(self):
        with mock.patch.object(pc, "BRIDGE", pc.ROOT / "bridge" / "does-not-exist.mjs"):
            self.assertIsNone(pc.sdk(perp(), "0x" + "00" * 20, 80000 * 10**18, False))

    def test_missing_node_modules_returns_none(self):
        with mock.patch.object(pc, "ROOT", pc.ROOT / "does-not-exist"):
            self.assertIsNone(pc.sdk(perp(), "0x" + "00" * 20, 80000 * 10**18, False))

    def test_precheck_falls_back_to_analytic(self):
        with mock.patch.object(pc, "sdk", return_value=None):
            r = pc.precheck(perp(), "0x" + "00" * 20, D(80000), D(25), D(3), False)
        self.assertEqual(r.source, "analytic")

    def test_sdk_answer_wins_when_present(self):
        venue = Precheck(True, True, True, D("0.002"), 200_000, "collateral", "sdk", 35_000_000)
        with mock.patch.object(pc, "sdk", return_value=venue):
            r = pc.precheck(perp(), "0x" + "00" * 20, D(80000), D(25), D(3), False)
        self.assertIs(r, venue)
        self.assertEqual(r.max_quantity_raw, 200_000)

    def test_precheck_sends_the_price_in_18_decimals(self):
        with mock.patch.object(pc, "sdk", return_value=None) as sdk:
            pc.precheck(perp(), "0x" + "00" * 20, D("80000.5"), D(25), D(3), False)
        self.assertEqual(sdk.call_args.args[2], 80_000_500_000_000_000_000_000)


class ZeroAddressTest(unittest.TestCase):
    def test_market_without_pool_is_still_analysable(self):
        m = Market(id="", base="BTC", quote="USDso", kind="PERP", base_decimals=8,
                   quote_decimals=18, pool="", margin_bank="0x2", mark_raw=80000 * 10**18)
        self.assertTrue(analytic(m, D(25), D(80000), D(3), False).ok)


if __name__ == "__main__":
    unittest.main()
