#!/usr/bin/env node
// Place one perp order through the official SDK. The only write path.
//
//   PRIVATE_KEY=0x.. node bridge/place.mjs --pool 0x.. --is-bid false \
//        --price 80865087300000000000000 --quantity 91000000 --type IOC [--dry-run]
//
// Amounts are raw integers: price is 18 decimals (always), quantity is the
// market's own baseDecimals. The SDK aligns nothing for you — the agent has
// already aligned both to tick/lot.
//
// Prints one JSON line with {ok, hash, orderId, filled, noFill, dryRun}.

import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES, ORDER_TYPE } from "@somnia-chain/markets-sdk";
import { hidekiTestnet } from "@somnia-chain/markets-sdk/chains";

const INDEXER = process.env.INDEXER_URL || "https://hideki-dev.smk.somnia.host/v1/graphql";
const WS = process.env.WS_RPC_URL || "wss://api.hideki2.infra.testnet.somnia.network/ws";
const RPC = process.env.RPC_URL || "https://api.hideki2.infra.testnet.somnia.network";

const TYPES = {
  LIMIT: ORDER_TYPE.LIMIT,
  GTC: ORDER_TYPE.LIMIT,
  FOK: ORDER_TYPE.FILL_OR_KILL,
  IOC: ORDER_TYPE.MARKET,
  POST_ONLY: ORDER_TYPE.POST_ONLY,
};

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
const price = BigInt(arg("price", "0"));
const quantity = BigInt(arg("quantity", "0"));
const isBid = String(arg("is-bid", "false")) === "true";
const typeName = String(arg("type", "IOC")).toUpperCase();
const dryRun = process.argv.includes("--dry-run");
const orderType = TYPES[typeName];

if (!pool || price <= 0n || quantity <= 0n || orderType === undefined) {
  out({ ok: false, error: "usage", detail: "need --pool --price --quantity --type=LIMIT|IOC|FOK|POST_ONLY" }, 1);
}

const key = process.env.PRIVATE_KEY;
if (!key && !dryRun) out({ ok: false, error: "no-key", detail: "PRIVATE_KEY is required unless --dry-run" }, 1);

try {
  const markets = new SomniaMarkets({
    indexerUrl: INDEXER,
    chain: hidekiTestnet,
    wsRpcUrl: WS,
    rpcUrl: RPC,
    addresses: SOMNIA_TESTNET_ADDRESSES,
    ...(key ? { privateKey: key } : {}),
  });

  if (dryRun) {
    const account = markets.walletAddress ?? null;
    out({ ok: true, dryRun: true, account, pool, isBid, price: String(price), quantity: String(quantity), type: typeName });
  }

  const res = await markets.trader.placePerpOrder({ pool, isBid, price, quantity, orderType });
  const filled = (res.fills ?? []).reduce((sum, f) => sum + (f.quantityFilled ?? 0n), 0n);
  out({
    ok: true,
    dryRun: false,
    hash: res.hash ?? null,
    orderId: res.orderId != null ? String(res.orderId) : null,
    filled: String(filled),
    noFill: filled === 0n,
  });
} catch (err) {
  const message = String(err?.message || err);
  out(
    {
      ok: false,
      error: "revert",
      detail: message.slice(0, 1200),
      benign: message.includes("ImmediateOrCancelNoFill"),
      name: message.includes("ImmediateOrCancelNoFill")
        ? "ImmediateOrCancelNoFill"
        : message.includes("InsufficientCollateral")
          ? "InsufficientCollateral"
          : null,
    },
    1,
  );
}
