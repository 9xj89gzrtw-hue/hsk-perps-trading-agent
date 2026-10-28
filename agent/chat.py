"""A conversational desk over the same numbers the CLI prints.

The point is not a chatbot. It is that a trader can ask "what is paying today?"
or "plan $2,000 at 3x" and get the *same* engine, with the *same* guards, in one
line. Intent routing is deterministic: a regex table, not a language model. That
means the answer is reproducible, the tests are hermetic, and a typo cannot
invent a market or send an order.

Every reply carries the data it was built from (`Reply.data`) and the metrics the
card renders. Nothing here writes or sends; the deploy answer is a *plan*, not a
transaction.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .funding import funding_view
from .models import Snapshot
from .precheck import precheck
from .strategy import Plan, rank

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLISHER = "0x72Aa6edDE71B94f6b99c8e52EdC02B5C75F74d1a"
ARTIFACT = ROOT / "build" / "contracts_SignalAdapter_sol_SignalAdapter.bin"
ZERO = "0x0000000000000000000000000000000000000000"

HELP = """I read the venue and answer in one line. Try:

  scan                      what is paying right now
  funding BTC               one market, normalised APR and basis
  plan 1000 3x              best cash-and-carry, sized
  check BTC 1000 3x         venue precheck, no order
  card                      render this answer as SVG + PNG
  deploy                    HSK gas, estimate, and the funding gate
  why                       the numbers behind the last answer

Amounts accept `$`, `usd`, and `,`; leverage accepts `3x` or `x3`.""".strip()

SUGGESTIONS = (
    "scan",
    "plan 1000 3x",
    "check BTC 1000 3x",
    "deploy",
    "card",
)


@dataclass(frozen=True)
class Reply:
    text: str
    intent: str = "help"
    question: str = ""
    metrics: tuple[tuple[str, str, str], ...] = ()
    suggestions: tuple[str, ...] = SUGGESTIONS
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "reply": self.text,
            "intent": self.intent,
            "question": self.question,
            "metrics": [{"label": a, "value": b, "tone": c} for a, b, c in self.metrics],
            "suggestions": list(self.suggestions),
            "data": self.data,
        }


def _money(value: Decimal, places: int = 2) -> str:
    if value.is_infinite():
        return "inf"
    return f"{value.quantize(Decimal(1).scaleb(-places)):,}"


def _apr(value: Decimal) -> str:
    return f"{value:+.2f}%"


def _fixed(raw, places: int = 6) -> str:
    """Format a decimal string from JSON without trailing zeros."""
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return str(raw)
    text = f"{value:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _answer(text: str, **kw) -> Reply:
    return Reply(text=text.strip(), **kw)


class ChatEngine:
    """Deterministic intent router. `loader` returns a Snapshot."""

    def __init__(
        self,
        loader,
        deploy_status=None,
        capital: Decimal = Decimal("100"),
        leverage: Decimal = Decimal("3"),
    ):
        self.loader = loader
        self.deploy_status = deploy_status or live_deploy_status
        self.capital = Decimal(capital)
        self.leverage = Decimal(leverage)
        self._snapshot: Snapshot | None = None
        self.last: Reply | None = None
        self.last_plan: Plan | None = None

    # -- data ---------------------------------------------------------------
    def snapshot(self, refresh: bool = False) -> Snapshot:
        if self._snapshot is None or refresh:
            self._snapshot = self.loader()
        return self._snapshot

    # -- routing ------------------------------------------------------------
    def reply(self, text: str, refresh: bool = False) -> Reply:
        question = " ".join((text or "").split())
        low = question.lower().strip()
        try:
            if not low or low in {"help", "?", "commands"} or "помощ" in low or "умеешь" in low:
                result = self._help(question)
            elif low.startswith("why") or low.startswith("почему"):
                result = self._why(question)
            elif low in {"card", "карточка", "card png"} or low.startswith("card "):
                result = self._card(question)
            elif self._is_deploy(low):
                result = self._deploy(question)
            elif low.startswith("check") or low.startswith("проверь"):
                result = self._check(question, refresh)
            elif low.startswith("plan") or low.startswith("план") or "carry" in low or "арбитраж" in low:
                result = self._plan(question, refresh)
            elif self._is_funding(low):
                result = self._funding(question, refresh)
            elif self._is_scan(low):
                result = self._scan(question, refresh)
            else:
                result = self._unknown(question)
        except Exception as exc:  # noqa: BLE001 - a chat turn must not kill the desk
            result = _answer(
                f"I could not read the venue: {exc}.\n"
                "The live indexer may be down; try again, or run with --fixture for the captured snapshot.",
                intent="error",
                question=question,
                data={"error": str(exc)},
            )
        self.last = result
        return result

    @staticmethod
    def _is_scan(low: str) -> bool:
        return any(word in low for word in ("scan", "market", "рынок", "ставк", "funding board", "status"))

    @staticmethod
    def _is_funding(low: str) -> bool:
        return "funding" in low or "apr" in low or "фандинг" in low

    @staticmethod
    def _is_deploy(low: str) -> bool:
        return any(word in low for word in ("deploy", "деплой", "gas", "газ", "hsk", "chain 177"))

    # -- intents ------------------------------------------------------------
    def _help(self, question: str) -> Reply:
        return _answer(HELP, intent="help", question=question,
                       metrics=(("engine", "deterministic", "blue"), ("writes", "none", "green")))

    def _scan(self, question: str, refresh: bool) -> Reply:
        snap = self.snapshot(refresh)
        rows = sorted(snap.perps, key=lambda m: -abs(m.funding_rate_raw))[:5]
        lines = [f"{snap.block:,} · {len(snap.perps)} perps · {len(snap.spots)} spots · {snap.at}"]
        for m in rows:
            view = funding_view(m)
            lines.append(
                f"{m.display:<12} mark {_money(m.mark, 4):>10}  APR {_apr(view.apr_percent):>9}"
                f"  basis {m.basis * 10000:+.2f} bps"
            )
        top = funding_view(rows[0]).apr_percent if rows else Decimal(0)
        return _answer(
            "\n".join(lines),
            intent="scan",
            question=question,
            metrics=(
                ("perps", str(len(snap.perps)), "blue"),
                ("spots", str(len(snap.spots)), "blue"),
                ("top APR", _apr(top), "green" if top >= 0 else "red"),
                ("block", f"{snap.block:,}", "muted"),
            ),
            data={"block": snap.block, "at": snap.at, "markets": [m.display for m in rows]},
        )

    def _funding(self, question: str, refresh: bool) -> Reply:
        snap = self.snapshot(refresh)
        wanted = self._market_hint(question, snap)
        market = snap.perp_by_display(wanted) if wanted else None
        if market is None:
            names = ", ".join(m.display for m in snap.perps[:8])
            missing = f"{wanted!r}" if wanted else "a market I could match"
            return _answer(
                f"I do not have {missing}. Markets: {names}.",
                intent="funding",
                question=question,
                suggestions=("scan", "plan 1000 3x"),
                data={"wanted": wanted},
            )
        view = funding_view(market)
        text = "\n".join(
            [
                f"{market.display} · {market.kind}",
                f"mark {_money(market.mark, 4)}  index {_money(market.index, 4)}  basis {market.basis * 10000:+.3f} bps",
                f"raw funding {market.funding_rate_raw} over {market.funding_window_sec}s"
                f" ({market.funding_interval_sec}s interval)",
                f"normalised {view.per_interval} per interval · {view.apr_percent:+.2f}% APR",
            ]
        )
        return _answer(
            text,
            intent="funding",
            question=question,
            metrics=(
                ("market", market.display, "blue"),
                ("APR", _apr(view.apr_percent), "green" if view.apr_percent >= 0 else "red"),
                ("basis", f"{market.basis * 10000:+.2f} bps", "muted"),
                ("OI", _money(market.open_interest, 2), "muted"),
            ),
            data={"market": market.display, "apr": str(view.apr_percent)},
        )

    def _plan(self, question: str, refresh: bool) -> Reply:
        snap = self.snapshot(refresh)
        capital, leverage = self._sizing(question)
        plans = rank(snap, capital, leverage)
        if not plans:
            return _answer(
                "No perp/spot pair on this venue right now.",
                intent="plan",
                question=question,
                metrics=(("capital", f"${_money(capital)}", "muted"), ("leverage", f"{leverage}x", "muted")),
                data={"capital": str(capital), "leverage": str(leverage)},
            )
        plan = plans[0]
        self.last_plan = plan
        state = "EXECUTABLE" if plan.executable else "WATCH ONLY"
        text = "\n".join(
            [
                f"{plan.pair.name} · {state}",
                f"notional ${_money(plan.notional)} · short {plan.pair.perp.display} · long {plan.pair.spot.display}",
                f"funding {_apr(plan.apr_percent)} · carry ${_money(plan.daily_carry, 3)}/day"
                f" · breakeven {_money(plan.breakeven_hours, 1)}h",
                f"margin ${_money(plan.perp_margin)} · liq ${_money(plan.liq_price or Decimal(0))}"
                f" · buffer {plan.liq_buffer * 100:.1f}%",
            ]
        )
        if not plan.executable and plan.reasons:
            text += f"\nwhy not: {plan.reasons[0]}"
        return _answer(
            text,
            intent="plan",
            question=question,
            metrics=(
                ("notional", f"${_money(plan.notional)}", "blue"),
                ("APR", _apr(plan.apr_percent), "green" if plan.apr_percent >= 0 else "red"),
                ("carry/day", f"${_money(plan.daily_carry, 3)}", "green"),
                ("liq buffer", f"{plan.liq_buffer * 100:.1f}%", "green" if plan.liq_buffer >= Decimal("0.15") else "amber"),
            ),
            data={
                "capital": str(capital),
                "leverage": str(leverage),
                "pair": plan.pair.name,
                "executable": plan.executable,
            },
        )

    def _check(self, question: str, refresh: bool) -> Reply:
        snap = self.snapshot(refresh)
        capital, leverage = self._sizing(question, default_capital=Decimal("20"))
        wanted = self._market_hint(question, snap)
        market = snap.perp_by_display(wanted) if wanted else (snap.perps[0] if snap.perps else None)
        if market is None:
            return _answer("No market to check.", intent="check", question=question)
        price = market.mark * (Decimal(1) + Decimal("0.001"))
        result = precheck(market, ZERO, price, capital, leverage, False)
        state = "placeable" if result.ok else "refused"
        text = "\n".join(
            [
                f"{market.display} · {state}",
                f"price {_money(price, 4)} · margin ${_money(capital)} · {leverage}x",
                f"max {_money(result.max_quantity, 6)} · limited by {result.limited_by} · source {result.source}",
            ]
        )
        if not result.ok:
            text += f"\nreason: {result.note or result.limited_by}"
        return _answer(
            text,
            intent="check",
            question=question,
            metrics=(
                ("market", market.display, "blue"),
                ("placeable", "yes" if result.ok else "no", "green" if result.ok else "red"),
                ("max qty", _money(result.max_quantity, 6), "blue"),
                ("source", result.source, "muted"),
            ),
            data={"market": market.display, "ok": result.ok, "limited_by": result.limited_by},
        )

    def _deploy(self, question: str) -> Reply:
        status = self.deploy_status()
        if status.get("error"):
            return _answer(
                f"HSK RPC: {status['error']}",
                intent="deploy",
                question=question,
                metrics=(("chain", "177", "muted"), ("gate", "unreachable", "red")),
                data=status,
            )
        ready = bool(status.get("ready"))
        cost = _fixed(status.get("cost_native", "?"), 6)
        balance = _fixed(status.get("balance_native", "?"), 6)
        short = _fixed(status.get("shortfall_native", "?"), 6)
        price = status.get("price_gwei") or "?"
        estimate = status.get("gas_estimate") or 0
        text = "\n".join(
            [
                f"HSK Chain 177 · SignalAdapter deploy plan",
                f"gas {price} gwei · estimate {estimate:,} gas · cost {cost} HSK",
                f"publisher {status.get('publisher', DEFAULT_PUBLISHER)} · balance {balance} HSK",
                ("gate OPEN — the deploy can be sent." if ready
                 else f"gate CLOSED — short {short} HSK."),
            ]
        )
        if not ready:
            text += "\nThis is a plan, not a transaction. Fund the publisher, then run scripts/hsk_deploy.py --json."
        return _answer(
            text,
            intent="deploy",
            question=question,
            metrics=(
                ("chain", "177", "blue"),
                ("gas gwei", str(price), "muted"),
                ("cost HSK", str(cost), "amber"),
                ("gate", "open" if ready else "closed", "green" if ready else "red"),
            ),
            data=status,
        )

    def _card(self, question: str) -> Reply:
        last = self.last
        if last is None:
            return _answer(
                "Nothing to draw yet. Ask `scan` or `plan 1000 3x` first.",
                intent="card",
                question=question,
                metrics=(("card", "waiting", "muted"),),
            )
        return _answer(
            f"Card for the last answer ({last.intent}).\nUse the SVG/PNG links in the web desk, or `chat --card out.svg`.",
            intent="card",
            question=question,
            metrics=(("source", last.intent, "blue"), ("format", "SVG + PNG", "green")),
            data={"source_intent": last.intent},
        )

    def _why(self, question: str) -> Reply:
        if self.last is None:
            return self._help(question)
        last = self.last
        metrics = "\n".join(f"  {a}: {b}" for a, b, _ in last.metrics) or "  (no metrics)"
        return _answer(
            f"That answer came from `{last.intent}`.\n{metrics}\n"
            f"data: {json.dumps(last.data, ensure_ascii=False)[:500]}",
            intent="why",
            question=question,
            metrics=(("based on", last.intent, "blue"),),
            data={"source": last.intent, "source_data": last.data},
        )

    def _unknown(self, question: str) -> Reply:
        return _answer(
            f"I did not map {question!r} to an action. I only answer from this desk's commands.",
            intent="unknown",
            question=question,
            suggestions=SUGGESTIONS,
            data={"question": question},
        )

    # -- parsing ------------------------------------------------------------
    @staticmethod
    def _market_hint(question: str, snap: Snapshot) -> str | None:
        upper = question.upper()
        for m in snap.perps:
            base = m.base.upper()
            if re.search(rf"\b{re.escape(base)}\b", upper):
                return m.display
        return None

    def _sizing(self, question: str, default_capital: Decimal | None = None) -> tuple[Decimal, Decimal]:
        capital = default_capital if default_capital is not None else self.capital
        leverage = self.leverage
        low = question.lower()

        lev = re.search(r"(?:x\s*(\d+(?:\.\d+)?))|(?:(\d+(?:\.\d+)?)\s*x)|(?:плеч\w*\s*(\d+(?:\.\d+)?))", low)
        if lev:
            raw = lev.group(1) or lev.group(2) or lev.group(3)
            parsed = self._decimal(raw)
            if parsed and Decimal(1) <= parsed <= Decimal(20):
                leverage = parsed

        money = re.search(r"(?:\$|usd\s*|capital\s*|депозит\s*|сумм\w*\s*)(\d[\d,]*(?:\.\d+)?)", low)
        if not money:
            money = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:usd|\$|долл\w*|бакс\w*)", low)
        if money:
            parsed = self._decimal(money.group(1))
            if parsed and parsed > 0:
                capital = parsed
        else:
            # A bare number is capital, unless it is the leverage ("3x" / "x3").
            for candidate in re.finditer(r"(?<![\dx.])(\d[\d,]*(?:\.\d+)?)(?!\s*x)", low):
                parsed = self._decimal(candidate.group(1))
                if parsed and parsed > 0:
                    capital = parsed
                    break
        return capital, leverage

    @staticmethod
    def _decimal(raw: str) -> Decimal | None:
        try:
            return Decimal(raw.replace(",", ""))
        except (InvalidOperation, AttributeError):
            return None


# --- live deploy status ----------------------------------------------------

_DEPLOY_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_SECONDS = 60


def live_deploy_status(chain: str = "mainnet", publisher: str = DEFAULT_PUBLISHER) -> dict:
    """The same arithmetic as scripts/hsk_deploy.py, cached for a chat turn.

    Kept here as a function so the web desk does not shell out per message, and so
    a test can inject a stub instead of hitting the public RPC.
    """
    now = time.time()
    cached = _DEPLOY_CACHE.get(chain)
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]

    from . import hsk

    target = hsk.HSK_TESTNET if chain == "testnet" else hsk.HSK_MAINNET
    rpc = hsk.Rpc(target.rpc, timeout=8, retries=0)
    status: dict = {"chain": target.chain_id, "publisher": publisher}
    try:
        status["block"] = rpc.block_number()
        estimate = hsk.DEPLOY_GAS_ESTIMATE
        source = "fallback"
        if ARTIFACT.exists():
            data = "0x" + ARTIFACT.read_text().strip() + hsk.enc_address(publisher).hex()
            try:
                estimate = rpc.estimate_gas({"from": publisher, "data": data})
                source = "eth_estimateGas"
            except hsk.RpcError:
                pass
        gate = hsk.deployment_gate(rpc, publisher, gas_estimate=estimate)
        status.update(
            {
                "gas_estimate": estimate,
                "gas_estimate_source": source,
                "price_gwei": str(Decimal(gate.gas_price_wei) / Decimal(10) ** 9),
                "base_fee_gwei": str(Decimal(gate.base_fee_wei) / Decimal(10) ** 9),
                "cost_native": str(gate.needed_native),
                "balance_native": str(gate.balance_native),
                "shortfall_native": str(gate.shortfall_native),
                "ready": gate.ready,
                "reason": gate.reason,
            }
        )
    except Exception as exc:  # noqa: BLE001
        status["error"] = str(exc)
    _DEPLOY_CACHE[chain] = (now, status)
    return status


def fixture_loader(path: str | None = None):
    from .indexer import load_fixture

    target = path or str(ROOT / "fixtures" / "markets_latest.json")

    def load():
        snapshot = load_fixture(target)
        # The JSON remembers it was captured live; the desk must not present a
        # replay as a live read. Keep the capture time, change the label.
        snapshot.source = "fixture"
        return snapshot

    return load


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
