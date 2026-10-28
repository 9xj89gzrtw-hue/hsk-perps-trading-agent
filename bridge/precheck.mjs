#!/usr/bin/env node
// Authoritative pre-trade size: the SDK's own getMaxPerpOrderSize.
//
// getMaxPerpOrderSize is not a view function. It reads the MarginBank, the
// pool parameters and the account's position at one block and binary-searches
// the largest size that clears the initial-margin gate, the position cap and
// the wallet top-up. Re-implementing it in another language produces a second
// opinion the pool does not share, so the Python agent shells out to this file.
//
//   node bridge/precheck.mjs --pool 0x.. --margin-bank 0x.. --account 0x.. \
//        --price 80865087300000000000000 --is-bid false --base-decimals 8
//
// Prints one JSON line. Exit 0 when the read succeeded, 1 when the SDK threw.

import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES } from "@somnia-chain/markets-sdk";
import { hidekiTestnet } from "@somnia-chain/markets-sdk/chains";

const INDEXER = process.env.INDEXER_URL || "https://hideki-dev.smk.somnia.host/v1/graphql";
const WS = process.env.WS_RPC_URL || "wss://api.hideki2.infra.testnet.somnia.network/ws";

function arg(name, fallback = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  if (i === -1 || i === process.argv.length - 1) return fallback;
  return process.argv[i + 1];
}

function out(payload, code = 0) {
  process.stdout.write(JSON.stringify(payload) + "\n");
  process.exit(code);
}

const pool = arg("pool");
const marginBank = arg("margin-bank");
const account = arg("account");
const price = BigInt(arg("price", "0"));
const isBid = String(arg("is-bid", "true")) === "true";

if (!pool || !marginBank || !account || price <= 0n) {
  out({ ok: false, error: "usage", detail: "need --pool --margin-bank --account --price" }, 1);
}

try {
  const markets = new SomniaMarkets({
    indexerUrl: INDEXER,
    chain: hidekiTestnet,
    wsRpcUrl: WS,
    addresses: SOMNIA_TESTNET_ADDRESSES,
    account,
  });
  const max = await markets.client.getMaxPerpOrderSize({
    pool,
    marginBank,
    account,
    isBid,
    price,
  });
  if (!max.priceable) {
    out({ ok: false, priceable: false, placeable: false, asOfBlock: Number(max.asOfBlock ?? 0), error: "stale-mark" });
  }
  out({
    ok: true,
    priceable: true,
    placeable: Boolean(max.placeable),
    maxQuantity: String(max.maxQuantity ?? 0n),
    limitedBy: String(max.limitedBy ?? ""),
    asOfBlock: Number(max.asOfBlock ?? 0),
    effectiveImfBps: max.effectiveImfBps != null ? String(max.effectiveImfBps) : null,
  });
} catch (err) {
  out({ ok: false, error: "sdk", detail: String(err?.stack || err).slice(0, 1200) }, 1);
}
