"""Typed market rows. All money stays Decimal; raw integers stay int."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def i(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def human(raw: int, decimals: int) -> Decimal:
    if decimals <= 0:
        return Decimal(raw)
    return Decimal(raw) / (Decimal(10) ** decimals)


@dataclass(frozen=True)
class PoolParams:
    """The last PerpPoolParametersEpoch for a pool."""

    pool: str
    initial_margin_bps: int = 1000
    maintenance_margin_bps: int = 500
    maker_fee_bps_times_1k: int = 0
    taker_fee_bps_times_1k: int = 0
    max_position_size: int = 0
    max_open_interest: int = 0
    timestamp: int = 0

    @property
    def imr(self) -> Decimal:
        return Decimal(self.initial_margin_bps) / Decimal(10_000)

    @property
    def mmr(self) -> Decimal:
        return Decimal(self.maintenance_margin_bps) / Decimal(10_000)

    @property
    def taker_fee(self) -> Decimal:
        # bpsTimes1k is bps x 1000, so 100_000 = 100 bps = 1% (the mainnet
        # builder cap reports exactly that number). Fraction = raw / 10**7.
        return Decimal(self.taker_fee_bps_times_1k) / Decimal(10_000_000)

    @property
    def maker_fee(self) -> Decimal:
        return Decimal(self.maker_fee_bps_times_1k) / Decimal(10_000_000)


@dataclass(frozen=True)
class Market:
    id: str
    base: str
    quote: str
    kind: str
    base_decimals: int
    quote_decimals: int
    pool: str
    margin_bank: str
    mark_raw: int = 0
    index_raw: int = 0
    last_raw: int = 0
    funding_rate_raw: int = 0
    funding_window_sec: int = 0
    funding_interval_sec: int = 0
    open_interest_raw: int = 0
    initial_margin_bps: int = 0
    tick_raw: int = 0
    lot_raw: int = 0
    min_qty_raw: int = 0
    volume_raw: int = 0
    trades: int = 0
    perp_status: str | None = None
    params: PoolParams | None = None

    # ---- derived, all in human units -------------------------------------
    @property
    def is_perp(self) -> bool:
        return self.kind.upper() == "PERP"

    @property
    def mark(self) -> Decimal:
        return human(self.mark_raw or self.last_raw, 18)

    @property
    def index(self) -> Decimal:
        return human(self.index_raw or self.last_raw, 18)

    @property
    def last(self) -> Decimal:
        return human(self.last_raw, 18)

    @property
    def open_interest(self) -> Decimal:
        return human(self.open_interest_raw, self.base_decimals)

    @property
    def volume(self) -> Decimal:
        return human(self.volume_raw, self.quote_decimals)

    @property
    def display(self) -> str:
        return f"{self.base}-PERP" if self.is_perp else f"{self.base}/{self.quote}"

    @property
    def tick(self) -> Decimal:
        return human(self.tick_raw, 18) if self.tick_raw else Decimal(0)

    @property
    def lot(self) -> Decimal:
        return human(self.lot_raw, self.base_decimals) if self.lot_raw else Decimal(0)

    @property
    def min_qty(self) -> Decimal:
        return human(self.min_qty_raw, self.base_decimals) if self.min_qty_raw else Decimal(0)

    @property
    def basis(self) -> Decimal:
        """(mark - index) / index. Positive means the perp trades rich."""
        if self.index <= 0:
            return Decimal(0)
        return (self.mark - self.index) / self.index

    @property
    def imr(self) -> Decimal:
        if self.params and self.params.initial_margin_bps:
            return self.params.imr
        if self.initial_margin_bps:
            return Decimal(self.initial_margin_bps) / Decimal(10_000)
        return Decimal("0.1")

    @property
    def mmr(self) -> Decimal:
        if self.params and self.params.maintenance_margin_bps:
            return self.params.mmr
        return self.imr / 2

    @property
    def taker_fee(self) -> Decimal:
        return self.params.taker_fee if self.params else Decimal(0)

    @classmethod
    def from_row(cls, row: dict[str, Any], params: PoolParams | None = None) -> "Market":
        kind = str(row.get("marketType") or "SPOT").upper()
        return cls(
            id=str(row.get("id") or ""),
            base=str(row.get("baseSymbol") or "?"),
            quote=str(row.get("quoteSymbol") or "USDso"),
            kind=kind,
            base_decimals=i(row.get("baseDecimals"), 18),
            quote_decimals=i(row.get("quoteDecimals"), 18),
            pool=str(row.get("poolAddress") or row.get("id") or ""),
            margin_bank=str(row.get("marginBank") or ""),
            mark_raw=i(row.get("markPrice")),
            index_raw=i(row.get("indexPrice")),
            last_raw=i(row.get("lastPrice")),
            funding_rate_raw=i(row.get("fundingRate")),
            funding_window_sec=i(row.get("fundingWindowSec")),
            funding_interval_sec=i(row.get("fundingIntervalSec")),
            open_interest_raw=i(row.get("openInterest")),
            initial_margin_bps=i(row.get("initialMarginBps")),
            tick_raw=i(row.get("tickSize")),
            lot_raw=i(row.get("lotSize")),
            min_qty_raw=i(row.get("minQuantity")),
            volume_raw=i(row.get("cumulativeQuoteVolume")),
            trades=i(row.get("tradeCount")),
            perp_status=row.get("perpStatus"),
            params=params,
        )


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    qty: Decimal


@dataclass(frozen=True)
class Book:
    market_id: str
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_bps(self) -> Decimal | None:
        mid = self.mid
        if not mid or self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_ask - self.best_bid) / mid * Decimal(10_000)

    def depth_notional(self, side: str, levels: int = 10) -> Decimal:
        rows = self.bids if side == "bid" else self.asks
        return sum((lvl.price * lvl.qty for lvl in rows[:levels]), Decimal(0))


@dataclass
class Snapshot:
    at: str
    block: int
    perps: list[Market] = field(default_factory=list)
    spots: list[Market] = field(default_factory=list)
    books: dict[str, Book] = field(default_factory=dict)
    source: str = ""

    def by_base(self, base: str, kind: str = "PERP") -> Market | None:
        pool = self.perps if kind.upper() == "PERP" else self.spots
        for market in pool:
            if market.base.upper() == base.upper():
                return market
        return None

    def perp_by_display(self, name: str) -> Market | None:
        wanted = name.upper().replace("-PERP", "")
        return self.by_base(wanted, "PERP")
