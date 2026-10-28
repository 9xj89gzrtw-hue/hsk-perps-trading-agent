"""Official Hideki indexer (Hasura/GraphQL) and the one RPC read we need.

No SDK dependency: the indexer is the same source the app reads, and keeping
the client to `urllib` means the analytics run on a clean machine with Python
3.10+ and nothing else. The order path is the part that uses the SDK, because
sizing and placement must be the venue's own code.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import INDEXER_URL, RPC_URL
from .models import Market, PoolParams, Snapshot

UA = "dreamdex-perps-agent/0.1 (+https://github.com/9xj89gzrtw-hue/dreamdex-perps-agent)"

MARKET_FIELDS = """
id baseSymbol quoteSymbol marketType perpRegistered perpStatus
markPrice indexPrice lastPrice fundingRate fundingWindowSec fundingIntervalSec
openInterest initialMarginBps baseDecimals quoteDecimals tickSize lotSize minQuantity
poolAddress marginBank stopRegistry cumulativeQuoteVolume tradeCount
"""

PERP_QUERY = """
query Perps {
  Market(where: {perpRegistered: {_eq: true}, marketType: {_eq: "PERP"}}) {
    %s
  }
}
""" % MARKET_FIELDS

SPOT_QUERY = """
query Spots {
  Market(where: {marketType: {_eq: "SPOT"}}) {
    %s
  }
}
""" % MARKET_FIELDS

PARAMS_QUERY = """
query Params {
  PerpPoolParametersEpoch(order_by: {timestamp: desc}, limit: 200) {
    pool initialMarginBps maintenanceMarginBps makerFeeBpsTimes1k takerFeeBpsTimes1k
    maxPositionSize maxOpenInterest timestamp
  }
}
"""

HISTORY_QUERY = """
query History($pool: String!) {
  FundingRateCandle(where: {pool: {_eq: $pool}}, order_by: {bucketStart: desc}, limit: 48) {
    bucketStart avgFundingRate8h minFundingRate8h maxFundingRate8h updateCount
    fundingWindowSec fundingIntervalSec indexPriceEnd
  }
}
"""


class IndexerError(RuntimeError):
    pass


def _post(url: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json", "user-agent": UA}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise IndexerError(f"HTTP {exc.code}: {exc.read()[:300]!r}") from exc
    except Exception as exc:  # noqa: BLE001
        raise IndexerError(f"{type(exc).__name__}: {exc}") from exc
    if "errors" in data:
        raise IndexerError(json.dumps(data["errors"])[:500])
    return data.get("data") or {}


def query(document: str, variables: dict[str, Any] | None = None, url: str = INDEXER_URL) -> dict[str, Any]:
    return _post(url, {"query": document, "variables": variables or {}})


def block_number(rpc_url: str = RPC_URL) -> int:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        rpc_url, data=body, headers={"content-type": "application/json", "user-agent": UA}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return int(json.load(resp)["result"], 16)


def _params_map() -> dict[str, PoolParams]:
    rows = query(PARAMS_QUERY).get("PerpPoolParametersEpoch") or []
    seen: dict[str, PoolParams] = {}
    for row in rows:
        pool = str(row.get("pool") or "").lower()
        if not pool or pool in seen:
            continue
        seen[pool] = PoolParams(
            pool=str(row.get("pool")),
            initial_margin_bps=int(row.get("initialMarginBps") or 0),
            maintenance_margin_bps=int(row.get("maintenanceMarginBps") or 0),
            maker_fee_bps_times_1k=int(row.get("makerFeeBpsTimes1k") or 0),
            taker_fee_bps_times_1k=int(row.get("takerFeeBpsTimes1k") or 0),
            max_position_size=int(row.get("maxPositionSize") or 0),
            max_open_interest=int(row.get("maxOpenInterest") or 0),
            timestamp=int(row.get("timestamp") or 0),
        )
    return seen


def fetch_snapshot(source: str = "live") -> Snapshot:
    params = _params_map()
    perp_rows = query(PERP_QUERY).get("Market") or []
    spot_rows = query(SPOT_QUERY).get("Market") or []
    perps = [Market.from_row(r, params.get(str(r.get("poolAddress") or "").lower())) for r in perp_rows]
    spots = [Market.from_row(r, params.get(str(r.get("poolAddress") or "").lower())) for r in spot_rows]
    try:
        block = block_number()
    except Exception:  # noqa: BLE001 - indexer data is still useful without it
        block = 0
    return Snapshot(
        at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        block=block,
        perps=perps,
        spots=spots,
        source=source,
    )


def fetch_history(pool: str, limit: int = 48) -> list[dict[str, Any]]:
    return query(HISTORY_QUERY, {"pool": pool}).get("FundingRateCandle") or []


def load_fixture(path: str | Path) -> Snapshot:
    payload = json.loads(Path(path).read_text())
    params = {
        str(p.get("pool") or "").lower(): PoolParams(
            pool=str(p.get("pool")),
            initial_margin_bps=int(p.get("initialMarginBps") or 0),
            maintenance_margin_bps=int(p.get("maintenanceMarginBps") or 0),
            maker_fee_bps_times_1k=int(p.get("makerFeeBpsTimes1k") or 0),
            taker_fee_bps_times_1k=int(p.get("takerFeeBpsTimes1k") or 0),
            max_position_size=int(p.get("maxPositionSize") or 0),
            max_open_interest=int(p.get("maxOpenInterest") or 0),
            timestamp=int(p.get("timestamp") or 0),
        )
        for p in payload.get("params") or []
    }
    perps = [Market.from_row(r, params.get(str(r.get("poolAddress") or "").lower())) for r in payload.get("perps") or []]
    spots = [Market.from_row(r, params.get(str(r.get("poolAddress") or "").lower())) for r in payload.get("spots") or []]
    return Snapshot(
        at=payload.get("at", "fixture"),
        block=int(payload.get("block") or 0),
        perps=perps,
        spots=spots,
        source=payload.get("source", "fixture"),
    )


def save_fixture(snapshot: Snapshot, path: str | Path) -> Path:
    path = Path(path)
    payload = {
        "at": snapshot.at,
        "block": snapshot.block,
        "source": snapshot.source,
        "perps": [_row(m) for m in snapshot.perps],
        "spots": [_row(m) for m in snapshot.spots],
        "params": [
            {
                "pool": m.params.pool,
                "initialMarginBps": m.params.initial_margin_bps,
                "maintenanceMarginBps": m.params.maintenance_margin_bps,
                "makerFeeBpsTimes1k": m.params.maker_fee_bps_times_1k,
                "takerFeeBpsTimes1k": m.params.taker_fee_bps_times_1k,
                "maxPositionSize": m.params.max_position_size,
                "maxOpenInterest": m.params.max_open_interest,
                "timestamp": m.params.timestamp,
            }
            for m in snapshot.perps
            if m.params
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))
    return path


def _row(m: Market) -> dict[str, Any]:
    return {
        "id": m.id,
        "baseSymbol": m.base,
        "quoteSymbol": m.quote,
        "marketType": m.kind,
        "markPrice": str(m.mark_raw),
        "indexPrice": str(m.index_raw),
        "lastPrice": str(m.last_raw),
        "fundingRate": str(m.funding_rate_raw),
        "fundingWindowSec": m.funding_window_sec,
        "fundingIntervalSec": m.funding_interval_sec,
        "openInterest": str(m.open_interest_raw),
        "initialMarginBps": m.initial_margin_bps,
        "baseDecimals": m.base_decimals,
        "quoteDecimals": m.quote_decimals,
        "tickSize": str(m.tick_raw),
        "lotSize": str(m.lot_raw),
        "minQuantity": str(m.min_qty_raw),
        "poolAddress": m.pool,
        "marginBank": m.margin_bank,
        "cumulativeQuoteVolume": str(m.volume_raw),
        "tradeCount": m.trades,
    }


def warm_cache(seconds: int = 0) -> None:
    if seconds > 0:
        time.sleep(seconds)
