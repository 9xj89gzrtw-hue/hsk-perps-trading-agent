# `perpStatus` is null on all 21 registered perp markets

**Area:** indexer / GraphQL schema
**Severity:** normal (safety-relevant for bots)
**Environment:** Somnia Hideki testnet, chain 50383, indexer `https://hideki-dev.smk.somnia.host/v1/graphql`, SDK `@somnia-chain/markets-sdk` 0.32.1, 2026-10-28

## Summary

Every row returned by the `Market` query has `perpRegistered: true` and `perpStatus: null`.
There is no value a client can branch on, and no documented enum.

## Reproduce

```bash
./scripts/repro_perp_status.sh
```

```
rows                    21
perpRegistered = true   21
perpStatus = null       21

BUG: perpStatus is null on all 21 registered perp markets
```

Minimal query:

```graphql
query { Market(where: {perpRegistered: {_eq: true}}) { baseSymbol perpStatus } }
```

## Expected

Either an enum a client can branch on (`ACTIVE`, `HALTED`, …), or the field absent from the
schema so nobody codes against it.

## Actual

`null` on 21/21 rows, including markets with live marks and non-zero open interest. The SDK does
not expose the field at all, so this only affects clients reading the indexer directly — which is
the documented way to list markets.

## Impact

A bot cannot tell a halted market from an active one. The only available proxy is
`markPrice > 0`, which stays positive on a market that stopped updating. A market maker that
quotes into a halted market can have orders resting through a resolution.

## Suggestion

Populate the enum, or drop the column. If the field is intentionally unused, saying so in the
schema comment is enough to stop integrators from depending on it.
