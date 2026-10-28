# Funding rate is per 8 h window, settled hourly — the denominator is undocumented (8× error)

**Area:** docs / funding semantics
**Severity:** normal (silent 8× overstatement)
**Environment:** Somnia Hideki testnet, chain 50383, all 21 perp markets, 2026-10-28

## Summary

`fundingRate` is a raw 1e18 integer. Every market reports `fundingWindowSec = 28800` and
`fundingIntervalSec = 3600`. The rate is denominated in the **window**, but nothing in the SDK
reference or the perps docs says which of the two fields is the denominator. Reading it as a
per-interval rate overstates APR by exactly `window/interval` — 8× on every live market.

## Reproduce

```bash
./scripts/repro_funding_scale.sh
```

```
BTC-PERP  rate 0.001425387556284271 per window
  fundingWindowSec      28800
  fundingIntervalSec    3600
  correct   APR  156.08%   (rate × 3 windows/day × 365)
  naive     APR  1248.64%   (rate × 24 intervals/day × 365)
  ratio     8.00×
```

## Expected

One sentence next to the field: "rate per `fundingWindowSec`", or a field named
`fundingRate8h`. The two numbers are both present in the row, which makes the ambiguity look
resolved when it is not.

## Actual

Both `fundingWindowSec` and `fundingIntervalSec` are returned, neither is documented as the
denominator, and the SDK's funding helpers do not state it either.

## Impact

A funding-carry bot ranks markets and sizes positions on the annualised number. An 8× error moves
a 156% carry to 1248%, which is the difference between "worth the fee break-even" and "obviously
wrong". It also makes any backtest using the wrong denominator unreproducible.

## Suggestion

Document the denominator on the field, or rename it. A non-zero testnet rate that steps every
hour would make the distinction observable without reading the contract.
