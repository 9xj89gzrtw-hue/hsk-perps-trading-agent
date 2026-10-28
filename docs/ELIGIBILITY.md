# Eligibility: what this repository does and does not satisfy

Written 2026-10-28 from the live hackathon page and the live chain, not from a summary. It
exists because the distance between "the code is done" and "the entry is valid" is where this
kind of submission usually dies, and it is cheaper to state it than to be told.

---

## HSK Chain Horizon Hackathon — Track 1: AI Agent Trading

$5,000 USDT (1st 2,500 / 2nd 1,500 / 3rd 1,000). Submission closes 2026-11-01 21:30 IST.

| requirement (verbatim from the page) | state | note |
| --- | --- | --- |
| "Projects must be original and built or rebuilt during the hackathon period" | **met, disclosed** | Engine reused from our DreamDEX work and disclosed in the README; HSK adapter, contract, signer, signal rehearsal and 51 HSK tests built now |
| "Projects should be built on HSK Chain" | **met** | the perp venue runs on chain 177 and was read live; `SignalAdapter` is deployed on HSK testnet 133 |
| AI agent as core engine, closed loop: monitor → analyse → signal → execute | **met off-chain, mirrored on-chain (testnet)** | 264 tests; the decision is published to `SignalAdapter`, with an expiry and a publisher check |
| Natural-language interface for positions / P&L / strategy changes | **met** | deterministic intent router (`agent/chat.py`) plus a local web desk with a market tape and SVG/PNG answer cards |
| Strict risk-control and stop-loss framework | **met** | liquidation buffer, margin health, `size_order` caps, refusal on unset router, dry-run default |
| "Make sure you deploy your smart contract on HSK Chain mainnet" | **BLOCKED** | testnet deployed and exercised; mainnet publisher holds 0 HSK and needs 0.218265 HSK |
| "projects that have not completed deployment ... forfeited their eligibility" | consequence | **mainnet eligibility is not satisfied yet** — the testnet deploy is evidence, not a substitute |
| In-person demo in Mumbai | **not available** | priority signal only, not a requirement |

### What is actually deployed

| item | value |
| --- | --- |
| network | HSK testnet, chain **133** |
| contract | `SignalAdapter` at `0x6595d7079cbdc72d52086ef7381364e23c5f00a9` |
| deploy tx | `0x4bb8794d3a16e13d2c422f45da6bc6f24300ea6c5b0f9d91a43e57c5ba716332` |
| deploy receipt | block 35,001,340 · status **1** · 344,625 gas · 0.00068925 HSK |
| publish tx | `0x82c8fa93a50f55bd79a74f3f78755855175156d1b4c675f0fafde244a4c300d4` |
| publish receipt | block 35,001,358 · status **1** · 90,831 gas · 0.000181662 HSK |
| readback | `get("btc-funding")` → `edgeBps 15720`, publisher `0x72Aa…4d1a` |
| live check | `verify.py` (online) reads `eth_getCode` + `publisher()` and requires both to match |

The publish row carried an 895-second TTL on purpose. It read `isActionable() == true`
immediately after the write and reads `false` now; the expiry is a feature, not a failed
transaction. The machine-readable record is [`../deployments/testnet.json`](../deployments/testnet.json).

### The mainnet gate

```
chain      hsk (177)  block 29,423,746
publisher  0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a
gas        base 500 gwei + tip 1 gwei = 501 gwei
estimate   348,527 gas (eth_estimateGas)  ·  cost 0.218265 HSK
gate       blocked: 0.000000 HSK on hsk (chain 177), need ~0.218265, short 0.218265 HSK
```

Eleven chains were checked for a balance on the publisher; all eleven returned zero. Relay has
no route for 177, and Gas.zip lists 177 with `outbound: false`. The faucet's reCAPTCHA was not
bypassed. The testnet deployment was funded from the Hardhat public dev account #0, which held
83.32 HSK on chain 133; only 0.02 HSK moved to the publisher, and that key was never used on
mainnet.

**Verdict: testnet-complete, mainnet-blocked.** One transfer of 0.2183 HSK to
`0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a` on chain 177 turns the only red row green. The
deploy path is already written, dry-run tested, and refuses to broadcast an unfunded plan.
Until that transfer exists, this entry is filed with the testnet evidence and an explicitly
open mainnet item — not with a claim that the rule is satisfied.

## AI Yako: Own Your AI (QVAC) — **not entered**

$3,500 USDt total; Cloud to Pocket $900 + Offline AI apps $700 = $1,600, matching the figure
this was briefed as. Deadline **2026-10-30 14:00 UTC**.

| requirement | state |
| --- | --- |
| "QVAC must be used meaningfully in every entry" | **not met** — Pocket Scholar runs llama.cpp, not `@qvac/sdk` |
| demo video with the internet disconnected | **not recorded** |
| source repo + setup + disclosure | available |

Pocket Scholar is a real offline app: no `INTERNET` permission in the manifest, a bundled
Qwen2.5 GGUF, and a BM25 index over 6,418 articles. But its inference layer is llama.cpp, and
the rules are explicit that meaningful QVAC integration is mandatory. Swapping the inference
layer to `@qvac/sdk@0.22.0` and recording an on-device offline demo is a two-day port plus a
device, not a README edit. Renaming it a QVAC entry without that port would be a false claim
in a submission, so this repository does not do it.

**Verdict: not entered.** The honest path is a real QVAC port for the next QVAC round.

## What would change either verdict

- HSK mainnet: fund `0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a` with 0.2183 HSK on chain 177,
  run `python3 scripts/hsk_deploy.py` until it prints `ready`, deploy with the audited signer,
  and replace the `deployments/testnet.json` reference with a mainnet record.
- AI Yako: port the inference layer to `@qvac/sdk`, record a disconnected demo, and disclose
  llama.cpp as the prior implementation.
