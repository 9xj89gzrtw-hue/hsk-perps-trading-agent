"""CLI contract: exit codes, JSON on stdout, and the dry-run gate."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from agent import cli
from agent.precheck import Precheck

D = Decimal


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class ScanTest(unittest.TestCase):
    def test_scan_fixture_prints_the_board(self):
        code, out, _ = run(["scan", "--fixture", "--limit", "3"])
        self.assertEqual(code, 0)
        self.assertIn("BTC-PERP", out)
        self.assertIn("APR%", out)

    def test_scan_json_is_machine_readable(self):
        code, out, _ = run(["scan", "--fixture", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["perps"]), 21)
        row = payload["perps"][0]
        self.assertIn("apr", row)
        self.assertIsInstance(row["fundingRate"], str)

    def test_limit_is_honoured(self):
        _, out, _ = run(["scan", "--fixture", "--limit", "2", "--json"])
        self.assertEqual(len(json.loads(out)["perps"]), 21)  # json ignores limit by design
        _, text, _ = run(["scan", "--fixture", "--limit", "2"])
        self.assertEqual(len([ln for ln in text.splitlines() if "-PERP" in ln]), 2)


class PlanTest(unittest.TestCase):
    def test_fixture_flag_never_touches_the_network(self):
        # Regression: --fixture used to fall through to the live indexer.
        with mock.patch.object(cli, "fetch_snapshot", side_effect=AssertionError("network")):
            code, out, _ = run(["plan", "--fixture"])
        self.assertEqual(code, 0)
        self.assertIn("BTC-PERP", out)

    def test_plan_fixture_finds_the_btc_carry(self):
        code, out, _ = run(["plan", "--fixture"])
        self.assertEqual(code, 0)
        self.assertIn("BTC-PERP / WBTC/USDso", out)
        self.assertIn("liq", out)

    def test_plan_json_lists_executable_first(self):
        code, out, _ = run(["plan", "--fixture", "--json"])
        self.assertEqual(code, 0)
        plans = json.loads(out)
        self.assertGreaterEqual(len(plans), 2)
        self.assertTrue(plans[0]["executable"])
        self.assertEqual(plans[0]["pair"], "BTC-PERP / WBTC/USDso")
        self.assertIn("breakevenHours", plans[0])

    def test_pairs_lists_both_hedges(self):
        code, out, _ = run(["pairs", "--fixture"])
        self.assertEqual(code, 0)
        self.assertIn("BTC-PERP / WBTC/USDso", out)
        self.assertIn("ETH-PERP / WETH/USDso", out)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.analytic = Precheck(True, True, True, D("0.0009"), 90_000, "leverage", "analytic", 0)

    def test_check_prints_the_precheck_json(self):
        with mock.patch.object(cli, "precheck", return_value=self.analytic):
            code, out, _ = run(["check", "--fixture", "--account", "0x" + "11" * 20])
        self.assertEqual(code, 0)
        body = out.split("\n", 1)[1]
        self.assertTrue(json.loads(body)["placeable"])

    def test_check_exits_one_when_not_placeable(self):
        blocked = Precheck(False, True, False, D(0), 0, "collateral", "sdk", 1)
        with mock.patch.object(cli, "precheck", return_value=blocked):
            code, out, _ = run(["check", "--fixture", "--account", "0x" + "11" * 20])
        self.assertEqual(code, 1)
        self.assertIn("collateral", out)

    def test_unknown_market_is_a_usage_error(self):
        code, _, err = run(["check", "--fixture", "--market", "NOPE-PERP", "--account", "0x" + "11" * 20])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)


class CardCliTest(unittest.TestCase):
    def test_card_writes_the_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.svg"
            code, out, _ = run(["card", "--fixture", "--out", str(path), "--tests", "7"])
            self.assertEqual(code, 0)
            self.assertTrue(path.exists())
            self.assertIn("wrote", out)
            self.assertIn("7/7 tests green", path.read_text())


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch = mock.patch.object(cli, "OUT", Path(self.tmp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.ok = Precheck(True, True, True, D("0.01"), 1_000_000, "collateral", "sdk", 35_000_000)

    def test_dry_run_places_nothing_and_journals(self):
        with mock.patch.object(cli, "precheck", return_value=self.ok):
            code, out, _ = run(["run", "--fixture", "--account", "0x" + "11" * 20])
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        record = json.loads((Path(self.tmp.name) / "last-run.json").read_text())
        self.assertFalse(record["executed"])
        self.assertTrue(record["precheck"]["placeable"])

    def test_precheck_no_blocks_the_run(self):
        blocked = Precheck(False, True, False, D(0), 0, "collateral", "sdk", 1)
        with mock.patch.object(cli, "precheck", return_value=blocked):
            code, out, _ = run(["run", "--fixture", "--account", "0x" + "11" * 20])
        self.assertEqual(code, 1)
        self.assertIn("refusing to send", out)

    def test_execute_without_yes_is_refused(self):
        with mock.patch.object(cli, "precheck", return_value=self.ok):
            code, _, err = run(["run", "--fixture", "--account", "0x" + "11" * 20, "--execute"])
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)

    def test_quantity_above_the_venue_max_is_refused(self):
        tiny = Precheck(True, True, True, D("0.000001"), 100, "collateral", "sdk", 1)
        with mock.patch.object(cli, "precheck", return_value=tiny):
            code, _, err = run(["run", "--fixture", "--account", "0x" + "11" * 20, "--execute", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("above venue max", err)


class ParserTest(unittest.TestCase):
    def test_a_command_is_required(self):
        with self.assertRaises(SystemExit):
            run([])

    def test_default_account_without_a_key_is_the_zero_address(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(cli._default_account(), "0x" + "00" * 20)

    def test_default_account_ignores_a_bad_key(self):
        with mock.patch.dict("os.environ", {"PRIVATE_KEY": "not-a-key"}):
            self.assertEqual(cli._default_account(), "0x" + "00" * 20)


if __name__ == "__main__":
    unittest.main()
