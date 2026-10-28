"""Synthetic markets with the live venue's shapes, for offline tests."""

from __future__ import annotations

from decimal import Decimal

from agent.models import Market, PoolParams

D18 = 10**18


def perp(
    base: str = "BTC",
    mark: str = "80000",
    index: str = "80010",
    funding: str = "0.001",
    window: int = 28800,
    interval: int = 3600,
    base_decimals: int = 8,
    im_bps: int = 500,
    lot: str = "0.00001",
    min_qty: str = "0.0001",
    pool: str = "0x" + "11" * 20,
    taker_fee: int = 0,
    max_position: int = 10**18,
) -> Market:
    params = PoolParams(
        pool=pool,
        initial_margin_bps=im_bps,
        maintenance_margin_bps=im_bps // 2,
        taker_fee_bps_times_1k=taker_fee,
        maker_fee_bps_times_1k=0,
        max_position_size=max_position,
    )
    return Market(
        id=pool,
        base=base,
        quote="USDso",
        kind="PERP",
        base_decimals=base_decimals,
        quote_decimals=18,
        pool=pool,
        margin_bank="0x" + "22" * 20,
        mark_raw=int(Decimal(mark) * D18),
        index_raw=int(Decimal(index) * D18),
        last_raw=int(Decimal(mark) * D18),
        funding_rate_raw=int(Decimal(funding) * D18),
        funding_window_sec=window,
        funding_interval_sec=interval,
        open_interest_raw=1000 * 10**base_decimals,
        initial_margin_bps=im_bps,
        tick_raw=D18 // 100,
        lot_raw=int(Decimal(lot) * (10**base_decimals)),
        min_qty_raw=int(Decimal(min_qty) * (10**base_decimals)),
        volume_raw=10**24,
        trades=1000,
        params=params,
    )


def spot(
    base: str = "WBTC",
    price: str = "80000",
    base_decimals: int = 8,
    lot: str = "0.00001",
    min_qty: str = "0.0001",
    pool: str = "0x" + "33" * 20,
) -> Market:
    return Market(
        id=pool,
        base=base,
        quote="USDso",
        kind="SPOT",
        base_decimals=base_decimals,
        quote_decimals=18,
        pool=pool,
        margin_bank="0x" + "22" * 20,
        last_raw=int(Decimal(price) * D18),
        mark_raw=int(Decimal(price) * D18),
        tick_raw=D18 // 100,
        lot_raw=int(Decimal(lot) * (10**base_decimals)),
        min_qty_raw=int(Decimal(min_qty) * (10**base_decimals)),
        volume_raw=10**24,
        trades=500,
    )
