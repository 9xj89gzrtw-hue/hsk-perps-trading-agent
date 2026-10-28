"""Indexer parsing, fixture round-trips and transport errors. No network."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from decimal import Decimal
from pathlib import Path
from unittest import mock

from agent import indexer
from agent.indexer import IndexerError, _params_map, _post, block_number, fetch_snapshot, load_fixture, save_fixture
from agent.models import PoolParams

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "markets_latest.json"


def fake_urlopen(payload: dict):
    class Ctx(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda *a, **k: Ctx(json.dumps(payload).encode())


class FixtureTest(unittest.TestCase):
    def test_loads_the_captured_universe(self):
        snap = load_fixture(FIXTURE)
        self.assertEqual(len(snap.perps), 21)
        self.assertEqual(len(snap.spots), 3)
        self.assertGreater(snap.block, 0)

    def test_attaches_pool_params(self):
        snap = load_fixture(FIXTURE)
        with_params = [m for m in snap.perps if m.params]
        self.assertTrue(with_params)
        self.assertEqual(with_params[0].params.initial_margin_bps, 2000)

    def test_round_trips_through_save(self):
        snap = load_fixture(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            path = save_fixture(snap, Path(tmp) / "snap.json")
            again = load_fixture(path)
        self.assertEqual(len(again.perps), len(snap.perps))
        self.assertEqual(again.perps[0].mark_raw, snap.perps[0].mark_raw)
        self.assertEqual(again.perps[0].funding_rate_raw, snap.perps[0].funding_rate_raw)
        self.assertEqual(again.block, snap.block)

    def test_saved_rows_keep_raw_integers_as_strings(self):
        snap = load_fixture(FIXTURE)
        row = indexer._row(snap.perps[0])
        self.assertIsInstance(row["markPrice"], str)
        self.assertEqual(int(row["markPrice"]), snap.perps[0].mark_raw)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_fixture("/nonexistent/snapshot.json")


class TransportTest(unittest.TestCase):
    def test_post_returns_the_data_envelope(self):
        with mock.patch("urllib.request.urlopen", fake_urlopen({"data": {"Market": [1]}})):
            self.assertEqual(_post("http://x", {"query": "q"}), {"Market": [1]})

    def test_post_raises_on_graphql_errors(self):
        with mock.patch("urllib.request.urlopen", fake_urlopen({"errors": [{"message": "nope"}]})):
            with self.assertRaises(IndexerError):
                _post("http://x", {"query": "q"})

    def test_post_raises_on_http_error(self):
        err = urllib.error.HTTPError("http://x", 500, "boom", {}, io.BytesIO(b"bad"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(IndexerError) as ctx:
                _post("http://x", {"query": "q"})
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_post_raises_on_connection_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(IndexerError):
                _post("http://x", {"query": "q"})

    def test_block_number_parses_hex(self):
        with mock.patch("urllib.request.urlopen", fake_urlopen({"result": "0x21b1a55"})):
            self.assertEqual(block_number("http://rpc"), 0x21B1A55)


class ParamsTest(unittest.TestCase):
    def rows(self):
        return {
            "PerpPoolParametersEpoch": [
                {"pool": "0xAB", "initialMarginBps": 2000, "maintenanceMarginBps": 1000,
                 "takerFeeBpsTimes1k": 0, "maxPositionSize": 1, "timestamp": 20},
                {"pool": "0xab", "initialMarginBps": 9999, "maintenanceMarginBps": 1, "timestamp": 10},
                {"pool": "0xcd", "initialMarginBps": 1000, "maintenanceMarginBps": 500,
                 "takerFeeBpsTimes1k": 1000, "timestamp": 5},
            ]
        }

    def test_keeps_the_newest_row_per_pool_case_insensitively(self):
        with mock.patch.object(indexer, "query", return_value=self.rows()):
            params = _params_map()
        self.assertEqual(set(params), {"0xab", "0xcd"})
        self.assertEqual(params["0xab"].initial_margin_bps, 2000)
        self.assertEqual(params["0xab"].taker_fee, 0)

    def test_fee_scale_on_the_indexer_row(self):
        with mock.patch.object(indexer, "query", return_value=self.rows()):
            params = _params_map()
        self.assertEqual(params["0xcd"].taker_fee, Decimal("0.0001"))

    def test_empty_indexer_yields_no_params(self):
        with mock.patch.object(indexer, "query", return_value={}):
            self.assertEqual(_params_map(), {})


class SnapshotTest(unittest.TestCase):
    def test_fetch_snapshot_joins_params_and_marks(self):
        perp_row = {
            "id": "0x1", "baseSymbol": "BTC", "quoteSymbol": "USDso", "marketType": "PERP",
            "poolAddress": "0xPOOL", "baseDecimals": 8, "quoteDecimals": 18,
            "markPrice": str(80000 * 10**18), "fundingRate": str(10**15),
            "fundingWindowSec": 28800, "fundingIntervalSec": 3600,
        }
        spot_row = dict(perp_row, id="0x2", baseSymbol="WBTC", marketType="SPOT", poolAddress="0xSPOT")

        def fake_query(document, variables=None, url=indexer.INDEXER_URL):
            if "PerpPoolParametersEpoch" in document:
                return {"PerpPoolParametersEpoch": [{"pool": "0xpool", "initialMarginBps": 1000}]}
            if '"PERP"' in document:
                return {"Market": [perp_row]}
            return {"Market": [spot_row]}

        with mock.patch.object(indexer, "query", side_effect=fake_query), \
             mock.patch.object(indexer, "block_number", return_value=7):
            snap = fetch_snapshot()
        self.assertEqual(snap.block, 7)
        self.assertEqual(len(snap.perps), 1)
        self.assertEqual(len(snap.spots), 1)
        self.assertEqual(snap.perps[0].params.initial_margin_bps, 1000)
        self.assertEqual(snap.perps[0].params.pool, "0xpool")

    def test_a_dead_rpc_does_not_lose_the_indexer_data(self):
        def fake_query(document, variables=None, url=indexer.INDEXER_URL):
            if "PerpPoolParametersEpoch" in document:
                return {}
            return {"Market": []}

        with mock.patch.object(indexer, "query", side_effect=fake_query), \
             mock.patch.object(indexer, "block_number", side_effect=OSError("down")):
            snap = fetch_snapshot()
        self.assertEqual(snap.block, 0)
        self.assertEqual(snap.perps, [])

    def test_snapshot_records_its_source(self):
        with mock.patch.object(indexer, "query", return_value={}), \
             mock.patch.object(indexer, "block_number", return_value=1):
            self.assertEqual(fetch_snapshot().source, "live")


class QueryShapeTest(unittest.TestCase):
    def test_market_fields_cover_what_the_model_reads(self):
        for field in ("fundingWindowSec", "fundingIntervalSec", "baseDecimals",
                      "lotSize", "minQuantity", "perpStatus", "poolAddress"):
            self.assertIn(field, indexer.MARKET_FIELDS)

    def test_params_row_instantiates(self):
        p = PoolParams(pool="0x1", initial_margin_bps=1000, maintenance_margin_bps=500)
        self.assertEqual(p.imr, Decimal("0.1"))
        self.assertEqual(p.mmr, Decimal("0.05"))


if __name__ == "__main__":
    unittest.main()
