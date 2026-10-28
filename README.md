# HSK Perps Trading Agent

An AI-agent trading engine for [HSK Chain](https://hsk.xyz) (chain **177**), built for the
**HSK Chain Horizon Hackathon — Track 1: AI Agent Trading**.

It watches perp funding, normalises it correctly, sizes both legs on the venue's own grid,
checks before it sends, and mirrors every decision into an on-chain `SignalAdapter` so the
history can be audited without trusting the operator.

```
201 tests · stdlib-only Python · chain 177 verified live · 0 dependencies for the analytics
```

---

## Status: the deploy gate is closed, and that is the whole story

HSK Chain Horizon requires it explicitly:

> Make sure you deploy your smart contract on HSK Chain mainnet, projects that have not
> completed deployment will be automatically deemed to have forfeited their eligibility
> for demo selection.

So the gate is code, not a promise. `scripts/hsk_deploy.py` refuses to hand out a deployment
plan it cannot pay for:

```console
$ python3 scripts/hsk_deploy.py
chain      hsk (177)  block 29,413,614
publisher  0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a
gas price  500.001000 gwei
artifact   1552 bytes
gate       blocked: 0.000000 HSK on hsk (chain 177), need ~1.300003, short 1.300003 HSK

NOT SUBMITTABLE: a hackathon entry that requires a mainnet deployment
is not a submission until this gate is green. Fund the publisher above.
```

The publisher holds **0 HSK**. 1.3 HSK at a 2× base-fee margin is the whole distance between
this repository and a deployable contract. Everything below the gate is finished and tested;
this paragraph is here so nobody has to discover that from a judge.

---

## What actually works, right now

| piece | state | evidence |
| --- | --- | --- |
| Chain adapter (177 / 133) | live | `HSK_LIVE=1 pytest tests/test_hsk.py` → 34 passed |
| keccak-256 + ABI encoding | verified against Ethereum vectors | `tests/test_keccak.py` |
| Selector ↔ Solidity agreement | checked by parsing the `.sol` | `test_selectors_come_from_the_solidity_source` |
| `SignalAdapter.sol` | compiles, 1552 bytes | `npx solc@0.8.26` → `build/` |
| Deploy gate + plan | refuses correctly | the console block above |
| Funding analytics engine | 155 tests | inherited, disclosed below |

## HSK Chain: what was verified, not assumed

Both ids were read from the live RPC on 2026-10-28, and the test suite asserts them:

```
eth_chainId  https://mainnet.hsk.xyz  -> 0xb1  (177)
eth_chainId  https://testnet.hsk.xyz  -> 0x85  (133)
```

`agent/hsk.py` holds the adapter: a stdlib JSON-RPC client, strict ABI encoders, the signal
key/row codec, and the deployment gate. Two details worth naming because they are the ones
that bite:

- **`hashlib.sha3_256` is not keccak256.** It pads with `0x06`; Ethereum pads with `0x01`.
  Every selector built from hashlib is silently wrong, and a call to a non-existent selector
  returns `0x`, which decodes as `False` and looks exactly like a stale signal. Hence
  `agent/keccak.py` and its vectors.
- **HSK's public RPC answers `403` to urllib's default user agent** while accepting the same
  request from curl. The live test caught it. Without the header, a failed call is
  indistinguishable from a zero balance, and the deploy gate would say "no funds" when it
  meant "cannot reach the node".

## `SignalAdapter.sol`

It does not trade. It publishes the decision, with the block it was made at and the block
after which it is stale.

```solidity
function publish(bytes32 key, int256 edgeBps, uint64 expiresAt) external onlyPublisher returns (uint256);
function revoke(bytes32 key) external onlyPublisher;
function get(bytes32 key) external view returns (Signal memory);
function isActionable(bytes32 key) external view returns (bool);
```

Why a deployment is worth anything here: an off-chain bot's track record is whatever its
operator says it is. A signal written once, never overwritten, and expiring on its own is a
claim you can check without trusting the author. `publish` refuses an expiry that has already
passed — a signal dead on arrival looks like evidence and is not.

## Provenance (required disclosure)

The funding-analytics engine is our own prior work, carried over unchanged and disclosed as
the hackathon rules require:

- **Reused:** `agent/` analytics (funding normalisation, risk, sizing, strategy, indexer,
  terminal, card) and its 155 tests, from `dreamdex-perps-agent`, previously built for
  DreamDEX/Somnia.
- **Built for this submission:** `agent/keccak.py`, `agent/hsk.py`, `contracts/SignalAdapter.sol`,
  `scripts/hsk_deploy.py`, `tests/test_keccak.py`, `tests/test_hsk.py` — 42 new tests.
- **Not reused:** no Somnia contract address, chain id, or market singleton is used on HSK.
  The HSK adapter does not import the Somnia config and refuses to invent an HSKswap router
  address it could not verify.

The engine is chain-agnostic on purpose: it reads a venue, and a venue is a config. What is
HSK-specific is the chain, the deploy, and the signal contract.

## Quickstart

```bash
# no install, no network
python3 verify.py --offline

# the HSK module, including the live chain-id assertions
python3 -m pytest tests/test_hsk.py tests/test_keccak.py -q
HSK_LIVE=1 python3 -m pytest tests/test_hsk.py -q

# compile the contract and ask whether it can be deployed
npx --yes solc@0.8.26 --bin --abi --optimize -o build contracts/SignalAdapter.sol
python3 scripts/hsk_deploy.py            # exits 1 while unfunded, by design
python3 scripts/hsk_deploy.py --json     # deploy_data, for an audited signer
```

`scripts/hsk_deploy.py` does not sign. Signing a mainnet deploy with a hand-rolled secp256k1
implementation is the kind of code that looks correct for months and then leaks a key through
a reused nonce. It emits `deploy_data` in the shape `cast` and `ethers` accept.

## Tests

```
$ python3 -m pytest tests -q
198 passed, 3 skipped (the 3 live tests are opt-in)

$ HSK_LIVE=1 python3 -m pytest tests/test_hsk.py -q
34 passed
```

New coverage: keccak vectors (empty, `abc`, a second rate block), ERC-20 selector and topic
known answers, selectors recomputed from the Solidity source text, two's-complement `int256`
in both directions, address/bytes32 validation, signal key determinism, calldata word layout
for both signs, struct decoding, the deploy gate's arithmetic and its refusal text, and that a
revert is surfaced on the first call rather than retried.

## Architecture

```
agent/
  keccak.py       keccak-256, selectors, event topics (pure Python)
  hsk.py          HSK chain 177/133, JSON-RPC, ABI, signal codec, deploy gate
  config.py       the perp venue's singletons (Somnia/Hideki) — untouched by the HSK path
  funding.py      window-vs-interval normalisation (the 8x guard)
  risk.py         PnL, liquidation, margin health, lot/tick alignment
  strategy.py     pair discovery, planning, ranking
  indexer.py      GraphQL + one RPC call, fixture round-trip
  precheck.py     revert decoder + SDK bridge / analytic fallback
  card.py         dependency-free SVG state card
  cli.py          scan / plan / pairs / check / card / run
contracts/
  SignalAdapter.sol   publishes the decision on-chain, immutable, expiring
scripts/
  hsk_deploy.py       gate, plan, and hand-off — never signs
  repro_*.sh          one-command reproducers for the four findings
tests/            201 tests, no network by default
```

## Safety model

- **Dry run is the default.** `run` needs both `--execute` and `--yes`.
- **The venue's answer wins** over our arithmetic, always.
- **Reverts are classified, not swallowed** — `0xd48c4403` benign skip, `0x3a23d825` halt and
  re-size, `0x782b2567` native-payout gas floor.
- **Chain-id mismatch aborts the plan.** A wrong RPC URL is caught before a transaction.
- **Unset router means no trade.** The HSK adapter refuses to build calldata against an address
  it could not verify, rather than defaulting to a guess.
- **No secrets in the repo.** Keys come from the environment; nothing is written to disk.

## Findings

Four reproducible issues from the underlying venue, with one-command reproducers in
`scripts/`: `perpStatus` is `null` on all 21 markets, the funding window is undocumented (the
8× trap), the docs' testnet chain id (50312) is not the chain the perps run on (50383), and the
fee field's scale is only inferable from the mainnet builder cap. See
[docs/FINDINGS.md](docs/FINDINGS.md).

## License

MIT.
