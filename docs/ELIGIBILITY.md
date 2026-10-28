# Eligibility: what this repository does and does not satisfy

Written 2026-10-28, from the live hackathon pages, not from a summary. It exists because the
distance between "the code is done" and "the entry is valid" is where this kind of submission
usually dies, and it is cheaper to state it than to be told.

---

## HSK Chain Horizon Hackathon — Track 1: AI Agent Trading

$5,000 USDT (1st 2,500 / 2nd 1,500 / 3rd 1,000). Submission closes 2026-11-01 21:30 IST.

| requirement (verbatim from the page) | state | note |
| --- | --- | --- |
| "Projects must be original and built or rebuilt during the hackathon period" | **met, disclosed** | Engine reused from our DreamDEX work and disclosed in the README; HSK adapter, contract and 40 tests built now |
| "Projects should be built on HSK Chain" | **met** | chain 177 verified live, adapter is HSK-native |
| AI agent as core engine, closed loop: monitor → analyse → signal → execute | **met off-chain** | 201 tests; execution path wired through the venue SDK |
| Natural-language interface for positions / P&L / strategy changes | **partial** | `cli.py` takes flags and `--json`; no conversational layer yet |
| Strict risk-control and stop-loss framework | **met** | liquidation buffer, margin health, `size_order` caps, refusal on unset router |
| "Make sure you deploy your smart contract on HSK Chain mainnet" | **BLOCKED** | publisher holds 0 HSK; needs ~1.3 HSK |
| "projects that have not completed deployment ... forfeited their eligibility" | consequence | **not submittable until the gate is green** |
| In-person demo in Mumbai | **not available** | priority signal only, not a requirement |

**Verdict: not submittable today.** One funding step — 1.3 HSK to `0x72Aa…4d1a` — turns every
red row green except the conversational interface, which is a scoped addition rather than a
blocker. Submitting without a deployed contract is worse than not submitting: the rules say
the entry is auto-forfeited, so the effort converts to nothing.

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
the rules are explicit that "projects that rely on a cloud AI API for that feature are not
eligible" — and equally explicit that meaningful QVAC integration is mandatory. Swapping the
inference layer to `@qvac/sdk@0.22.0` and recording an on-device offline demo is a two-day port
plus a device, not a README edit. Renaming it a QVAC entry without that port would be a false
claim in a submission, so this repository does not do it.

**Verdict: not entered.** The honest path is a real QVAC port for the next QVAC round.

## What would change either verdict

- HSK: fund `0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a` with ~1.3 HSK on chain 177, run
  `python3 scripts/hsk_deploy.py` until it prints `ready`, then deploy with an audited signer
  and add the contract address here.
- AI Yako: port the inference layer to `@qvac/sdk`, record a disconnected demo, and disclose
  llama.cpp as the prior implementation.
