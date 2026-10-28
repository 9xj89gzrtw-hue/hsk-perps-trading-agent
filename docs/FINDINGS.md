# Findings

Everything below was hit while building the agent, against live Hideki (chain 50383) on
2026-10-28. Each item has a one-command reproducer under `scripts/`. No security issues are
included.

| # | area | summary | severity |
| --- | --- | --- | --- |
| 1 | indexer schema | `perpStatus` is `null` on all 21 markets | normal |
| 2 | docs / semantics | funding rate is per 8 h window; annualising per interval is 8× too high | normal |
| 3 | docs | the docs' testnet chain id (50312) is not the chain the perps run on (50383) | normal |
| 4 | SDK / docs | `takerFeeBpsTimes1k` scale is undocumented; the obvious reading is 100× off | normal |

---

## 1. `perpStatus` is `null` on every market

**Repro:** `./scripts/repro_perp_status.sh`

```
$ ./scripts/repro_perp_status.sh
BUG: perpStatus is null on all 21 registered perp markets
  expected  an enum a client can branch on (active / halted / …)
  actual    null on 21/21 rows, while perpRegistered is true on all of them
  impact    a bot cannot tell a halted market from an active one
```

The field is part of the public `Market` row, sits next to `perpRegistered: true`, and is `null`
everywhere. A client that wants to avoid quoting into a halted market has to infer it from
`markPrice > 0`, which is also true for a market that stopped updating.

The SDK does not expose the field at all, so this only affects clients that read the indexer
directly — which the app itself does.

**Suggestion:** populate the enum, or drop the column so nobody codes against it.

---

## 2. Funding is quoted per 8 h window, settled hourly — and the docs never say so

**Repro:** `./scripts/repro_funding_scale.sh`

```
$ ./scripts/repro_funding_scale.sh
BTC-PERP  rate 0.001425387556284271 per window
  fundingWindowSec      28800
  fundingIntervalSec    3600
  correct   APR  156.08%   (rate × 3 windows/day × 365)
  naive     APR  1248.64%   (rate × 24 intervals/day × 365)
  ratio     8.00×
```

`fundingRate` is a raw integer with `fundingWindowSec = 28800` and `fundingIntervalSec = 3600`.
Both fields are present, but nothing in the SDK reference or the perps docs states which one the
rate is denominated in. Reading it as a per-interval rate overstates APR by `window/interval` —
exactly 8× on every live market, because every market uses the same 8 h/1 h pair.

The error is silent and flattering: a 156% carry looks like 1248%, which changes ranking and
position sizing. This agent divides by the window in exactly one place (`agent/funding.py`) and
pins the invariance in `tests/test_funding.py::test_changing_interval_does_not_change_apr`.

**Suggestion:** document the denominator next to the field, or rename it `fundingRate8h`.

---

## 3. The documented testnet chain id is not the one the perps run on

**Repro:** `./scripts/repro_chain_id.sh`

```
$ ./scripts/repro_chain_id.sh
BUG: docs say testnet is 50312, the perps RPC answers 50383
  eth_chainId  0xc4cf = 50383
  docs         50312 ("SIWE Chain ID must match the network… 5031 mainnet, 50312 testnet")
  impact       SIWE login built from the docs signs the wrong chain id
```

The bot-kit's gotchas note tells integrators to derive the SIWE chain id from the network and
gives 50312 for testnet. The Hideki RPC used by the perps stack — and the SDK's `hidekiTestnet`
chain object — is 50383. A signer built from the documented number fails login on the perps
deployment while looking correct in review.

**Suggestion:** name the networks explicitly (Shannon 50312 vs Hideki 50383) instead of
"testnet".

---

## 4. `takerFeeBpsTimes1k` has no documented scale, and the obvious reading is 100× off

**Repro:** `./scripts/repro_fee_scale.sh`

```
$ ./scripts/repro_fee_scale.sh
BUG: fee scale is only inferable from the mainnet builder cap
  field        takerFeeBpsTimes1k (bps × 1000 ⇒ fraction = raw / 1e7)
  reference    getMaxBuilderFeeBpsTimes1k() = 100000 is documented as a 1% cap
  naive read   raw / 1e5  ⇒ 100000 reads as 100%, not 1%
  testnet      every market reports 0, so the error cannot be caught locally
```

Every testnet market reports `takerFeeBpsTimes1k = 0`, so an off-by-100 fee bug is invisible until
mainnet. The only published anchor is the builder cap: 100000 = 1%. That implies
`fraction = raw / 10^7`, i.e. the field is bps × 1000. Nothing in the SDK reference states it.

This agent had the wrong divisor (`/1e5`) until the reference number was cross-checked; the fix
and a regression test are in `agent/models.py` and `tests/test_models.py::test_fee_scale_matches_the_venue`.

**Suggestion:** document the unit on the struct field, and return a non-zero testnet fee on one
market so the path is exercised.

---

## Not filed

- `getMaxPerpOrderSize` returning `ok: true` with `placeable: false` for an unfunded account is
  correct but easy to misread; the bridge surfaces both fields and the CLI checks `placeable`.
  Not a defect.
- `openInterest` is denominated in base units and matches `baseDecimals` on every market we
  checked (BTC 8, TAO 9, the rest 18). No finding.
