"""Network and protocol constants for the Hideki testnet (chain 50383)."""

from __future__ import annotations

from dataclasses import dataclass

CHAIN_ID = 50383
NETWORK = "hideki"

RPC_URL = "https://api.hideki2.infra.testnet.somnia.network"
WS_URL = "wss://api.hideki2.infra.testnet.somnia.network/ws"
INDEXER_URL = "https://hideki-dev.smk.somnia.host/v1/graphql"
EXPLORER = "https://explorer.testnet.somnia.network/tokyo"

# Singletons, copied from the developer docs (Perps on Testnet).
MARGIN_BANK = "0x478769E1177C0773c43A593DCCc4Ad15F15F1394"
COLLATERAL = "0xed7993A74dAe1e6656B704A424DcD8711aAf4F72"  # USDso
PERP_POOL_FACTORY = "0xE631fAEE547baeE07353E290f0562ea67d856560"
LIQUIDATION_ENGINE = "0xA66d3C24Ad4a389d863846905D8173e1017e9F4D"
INSURANCE_FUND = "0x50826894D47C6D4f3BeA38BFDC7Dc5c5be171b80"

# A perp order reverts rather than returning a status. These are the two the
# venue actually throws at a bot, plus the native-payout guard for completeness.
ERRORS: dict[str, tuple[str, str]] = {
    "0xd48c4403": (
        "ImmediateOrCancelNoFill",
        "IOC found nothing to cross. Benign: the book moved between read and send. "
        "Skip the cycle; do not re-send the same price.",
    ),
    "0x3a23d825": (
        "InsufficientCollateral",
        "The size is wrong for the market's decimals or the margin is short. Halt, "
        "re-read baseDecimals and the in-bank balance, then re-size. Never just retry.",
    ),
    "0x782b2567": (
        "InsufficientGasForPayout",
        "Native-base buy below the 5M gas floor. Only spot SOMI buys; a perp should "
        "never reach it, which makes it a real finding if it does.",
    ),
}

# Contract error text -> selector, for the cases where the SDK surfaces a name
# but the receipt only carries data.
NAMES = {v[0]: k for k, v in ERRORS.items()}

YEAR_SECONDS = 365 * 24 * 60 * 60


@dataclass(frozen=True)
class Network:
    name: str = NETWORK
    chain_id: int = CHAIN_ID
    rpc: str = RPC_URL
    ws: str = WS_URL
    indexer: str = INDEXER_URL
    explorer: str = EXPLORER


HIDEKI = Network()
