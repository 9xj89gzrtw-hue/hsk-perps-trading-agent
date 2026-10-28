# `takerFeeBpsTimes1k` scale is undocumented; the obvious reading is 100× off

**Area:** SDK / docs, pool parameters
**Severity:** normal (invisible on testnet)
**Environment:** Somnia Hideki testnet, chain 50383, `PerpPoolParametersEpoch`, 2026-10-28

## Summary

`PerpPoolParametersEpoch` returns `takerFeeBpsTimes1k` / `makerFeeBpsTimes1k`. The name suggests
bps × 1000, which makes the fraction `raw / 1e7`. Nothing states the unit, and the obvious
alternative (`raw / 1e5`, treating the value as bps × 100) is 100× too large.

Every testnet market reports **0**, so a wrong divisor cannot be caught locally. The only public
anchor is the builder cap: `getMaxBuilderFeeBpsTimes1k() = 100000`, documented as a **1%** cap —
which confirms `raw / 1e7`.

## Reproduce

```bash
./scripts/repro_fee_scale.sh
```

```
pools                   21
non-zero taker fee      0
reference               getMaxBuilderFeeBpsTimes1k() = 100000 is documented as a 1% cap
implied scale           fraction = raw / 10^7
naive reading           raw / 10^5 -> 1 (100%, not 1%)
```

## Expected

The unit documented on the struct field (`bps × 1000; fraction = raw / 1e7`), and at least one
testnet market with a non-zero fee so the path is exercised.

## Actual

All testnet fees are zero and the unit exists only in the field name. A bot that divides by `1e5`
computes a 1% fee as 100%, decides every carry is unprofitable, and never trades — or, in the
other direction, sizes against a fee 100× too small.

We shipped the wrong divisor (`/1e5`) until we cross-checked against the builder cap; the fix and
a regression test are in `agent/models.py` and
`tests/test_models.py::test_fee_scale_matches_the_venue`.

## Impact

Silent mispricing of every strategy that models fees. Because the live value is zero, the bug is
undetectable in testnet integration tests and only appears on mainnet.

## Suggestion

Document the scale on the field, and return a non-zero taker fee on one testnet market (even 1 bp)
so integrators can verify their conversion against a known trade.
