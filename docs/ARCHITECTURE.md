# Architecture

## One screen

```
                 ┌──────────────────────────────┐
                 │  Hasura indexer (GraphQL)    │  21 perp + 3 spot rows
                 │  hideki-dev.smk.somnia.host  │  mark, index, funding, OI,
                 └──────────────┬───────────────┘  decimals, lot, min, pool
                                │ urllib
                 ┌──────────────▼───────────────┐
                 │  agent/indexer.py            │  Snapshot + PoolParams
                 └──────────────┬───────────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        │                       │                        │
┌───────▼────────┐   ┌──────────▼─────────┐   ┌──────────▼─────────┐
│ funding.py     │   │ strategy.py        │   │ risk.py            │
│ window→interval│   │ pairs, plans, rank │   │ liq, margin, grid  │
└───────┬────────┘   └──────────┬─────────┘   └──────────┬─────────┘
        └───────────────────────┼────────────────────────┘
                                │ Plan
                 ┌──────────────▼───────────────┐
                 │  terminal.py / card.py       │  stdout, out/card.svg
                 └──────────────┬───────────────┘
                                │ only on --execute --yes
                 ┌──────────────▼───────────────┐
                 │  bridge/precheck.mjs         │  getMaxPerpOrderSize (SDK)
                 │  bridge/place.mjs            │  placePerpOrder (SDK)
                 └──────────────────────────────┘
```

## Why Python for analytics, Node for the write path

The interesting part of this agent is arithmetic that a reviewer must be able to audit:
funding normalisation, liquidation distance, capital split, lot alignment. That belongs in a
small, dependency-free language surface. `agent/` imports nothing outside the standard library,
so `python3 verify.py --offline` works on a fresh machine and the tests never touch a network.

The write path is different. `getMaxPerpOrderSize` is **not** a contract view: the SDK reads the
MarginBank, the pool parameters and the account position at one block, then binary-searches the
largest size that clears the initial-margin gate, the position cap and the wallet top-up. Any
Python re-implementation is a second opinion the pool does not share. So the agent shells out to
`bridge/precheck.mjs`, which calls the SDK, and treats the Python answer as a labelled fallback
(`source: "analytic"`) for machines without Node.

## The formulas

### Funding

The indexer stores `fundingRate` as a raw 1e18 integer covering `fundingWindowSec` (28 800 s on
every live market), settled every `fundingIntervalSec` (3 600 s). The rate that actually settles
each hour is truncated integer division:

```
per_interval_raw = fundingRate * intervalSec // windowSec
APR              = fundingRate / 1e18 * 31_536_000 / windowSec
daily            = per_interval * (86_400 / intervalSec)
```

Annualising `per_interval` instead of `rate` overstates by `window/interval` — exactly 8× here.
`agent/funding.py` is the only place the division happens, and `tests/test_funding.py` pins both
the number and the invariance (changing the interval must not change APR).

### Liquidation (isolated, linear, USDso-margined)

```
equity_long  = margin + (mark - entry) * qty
equity_short = margin + (entry - mark) * qty
liquidatable when equity = mmr * mark * qty

short: liq = (entry + margin/qty) / (1 + mmr)
long:  liq = (entry - margin/qty) / (1 - mmr)
```

Fees and funding are excluded, so the result is the *optimistic* liquidation price; the real one
arrives earlier. A long whose margin exceeds its entry notional is liquidated at zero, and the
function returns `0` instead of a negative price.

### Sizing

```
max_notional = min(capital / (1 + 1/leverage),  equity / imr,  position_cap * price)
qty          = floor(notional / price / lot) * lot
reject       if qty < minQuantity
```

`leverage` can only lower size below the market's own `1/imr`; it can never raise it.

### Hedge ratio

Both legs are sized to the same notional, then re-aligned to the spot lot and back to the perp
lot. The residual delta is at most one perp lot, which is why the plan reports `spot qty` and
`perp qty` separately rather than assuming they are equal.

## Decisions and trade-offs

| decision | why | cost |
| --- | --- | --- |
| Only positive funding is executable | the reverse trade needs a spot short; the venue lists spot but no borrow | half the funding board is "watch only" |
| No cross-margin assumption | the two wallets are separate; a perp call can fire while spot is in profit | larger capital requirement per unit of carry |
| Refuse on any blocker | a half-executed carry is a naked directional position | the agent often does nothing |
| Shell out to the SDK | sizing must match the venue | Node + 113 MB `node_modules` for the write path |
| Fixture-first CLI | reproducibility and offline tests | fixtures go stale; `verify.py` checks the live read separately |

## Failure modes

- **Indexer down** → `IndexerError`, exit 4, nothing sent.
- **RPC down** → block `0`; the snapshot still loads, and `verify.py` fails the live step.
- **Stale mark** → `priceable: false`, precheck refuses.
- **Book moved between read and send** → `0xd48c4403`, classified benign, cycle skipped.
- **Wrong decimals** → `0x3a23d825` (insufficient collateral), halt and re-read, never retry.
- **Bridge missing** → analytic bound with a `source` label the caller can check.

## Extension points

- `strategy.HEDGE_ALIASES` — add a perp/spot alias when the venue lists a new hedge.
- `funding.FundingView` — swap in a historical series (`fetch_history`) for a carry forecast.
- `precheck.decode_revert` — add a selector to `config.ERRORS`; the decoder and tests pick it up.
- `card.render` — the SVG is plain string composition; no browser, no fonts, no network.
