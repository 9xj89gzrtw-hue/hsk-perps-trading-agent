# Documented testnet chain id (50312) is not the chain the perps run on (50383)

**Area:** docs / SIWE, network configuration
**Severity:** normal (login fails, looks like a key problem)
**Environment:** Somnia Hideki testnet, RPC `https://api.hideki2.infra.testnet.somnia.network`, 2026-10-28

## Summary

The bot-kit's gotchas section says: *"the `Chain ID` in the SIWE message must match the network
you're signing txs for: **5031 mainnet, 50312 testnet**"*. The RPC that serves the perps
deployment answers `0xc4cf` = **50383**. A SIWE signer built from the documented number signs the
wrong chain id.

## Reproduce

```bash
./scripts/repro_chain_id.sh
```

```
RPC                    https://api.hideki2.infra.testnet.somnia.network
eth_chainId            0xc4cf = 50383
web3_clientVersion     somnia-f9f7a5cd6b146b6-release
docs, mainnet          5031
docs, testnet          50312

BUG: docs say testnet is 50312, the perps RPC answers 50383
```

## Expected

The docs name the network that the perps stack actually uses, or distinguish the two testnets
(Shannon 50312 vs Hideki/Tokyo 50383) instead of saying "testnet".

## Actual

"testnet" is used for both, while the SDK's `hidekiTestnet` chain object and the RPC agree on
50383. The mismatch only surfaces at login, after the wallet has signed.

## Impact

An integrator following the docs gets `invalid chain id` / signature rejection on the perps
deployment and starts debugging keys or SIWE formatting. We lost time on exactly this and only
resolved it by reading `eth_chainId` from the RPC.

## Suggestion

Replace "5031 mainnet, 50312 testnet" with a table:

| network | chain id | RPC |
| --- | --- | --- |
| mainnet | 5031 | … |
| Shannon testnet | 50312 | … |
| Hideki (perps) | 50383 | `https://api.hideki2.infra.testnet.somnia.network` |
