"""Funding, read the way the pool means it.

The stored rate covers the CALCULATION WINDOW (`fundingWindowSec`, 8h on every
live market), not one settlement interval (`fundingIntervalSec`, 1h). Treating
it as a per-interval rate overstates the annual figure by window/interval — on
Hideki, exactly 8x. This module is the single place that division happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .config import YEAR_SECONDS
from .models import Market

ONE = Decimal(10**18)


@dataclass(frozen=True)
class FundingView:
    rate_raw: int
    window_sec: int
    interval_sec: int

    @property
    def known(self) -> bool:
        return self.window_sec > 0 and self.interval_sec > 0 and self.rate_raw != 0

    @property
    def rate(self) -> Decimal:
        """Fraction per calculation window (0.001 = 0.1%)."""
        return Decimal(self.rate_raw) / ONE

    @property
    def per_interval_raw(self) -> int:
        """What actually settles each interval, truncated like the contract."""
        if not self.known:
            return 0
        return self.rate_raw * self.interval_sec // self.window_sec

    @property
    def per_interval(self) -> Decimal:
        return Decimal(self.per_interval_raw) / ONE

    @property
    def apr(self) -> Decimal:
        if not self.known:
            return Decimal(0)
        return self.rate * Decimal(YEAR_SECONDS) / Decimal(self.window_sec)

    @property
    def apr_percent(self) -> Decimal:
        return self.apr * 100

    @property
    def daily(self) -> Decimal:
        """Fraction earned per day on the notional, at the current rate."""
        if not self.known:
            return Decimal(0)
        intervals = Decimal(86_400) / Decimal(self.interval_sec)
        return self.per_interval * intervals

    @property
    def positive(self) -> bool:
        return self.rate_raw > 0

    @property
    def paid_side(self) -> str:
        """The side that RECEIVES funding. Positive funding: longs pay shorts."""
        return "short" if self.rate_raw > 0 else "long"

    @property
    def paying_side(self) -> str:
        return "long" if self.rate_raw > 0 else "short"


def view_of(rate_raw: int, window_sec: int, interval_sec: int) -> FundingView:
    return FundingView(rate_raw, window_sec, interval_sec)


def funding_view(market: Market) -> FundingView:
    return view_of(market.funding_rate_raw, market.funding_window_sec, market.funding_interval_sec)


def annualize(rate_raw: int, window_sec: int) -> Decimal:
    if window_sec <= 0:
        return Decimal(0)
    return Decimal(rate_raw) / ONE * Decimal(YEAR_SECONDS) / Decimal(window_sec)


def per_interval_raw(rate_raw: int, window_sec: int, interval_sec: int) -> int:
    if window_sec <= 0 or interval_sec <= 0:
        return 0
    return rate_raw * interval_sec // window_sec


def carry_usd(notional: Decimal, view: FundingView, hours: Decimal) -> Decimal:
    """Funding received on `notional` over `hours`, sign follows the paid side.

    A negative result means the position pays instead of earning.
    """
    if not view.known or view.interval_sec <= 0:
        return Decimal(0)
    intervals = hours * Decimal(3600) / Decimal(view.interval_sec)
    return notional * view.per_interval * intervals
