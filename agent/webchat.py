"""The local web desk: stdlib HTTP, one page, no external assets.

`python3 -m agent chat --web` starts this on 127.0.0.1. It is deliberately not a
framework: the whole surface is four routes, the body is capped, and the card is
rendered server-side so the browser never needs a charting library.

Routes
    GET  /                     the desk + market tape
    POST /api/chat             {"message": "..."} -> reply + card links
    GET  /api/markets          the tape rows the page renders, as JSON
    GET  /api/card/<id>.svg    the card for that turn
    GET  /api/card/<id>.png    1200x760 PNG
    GET  /api/health           liveness, card count, deployment summary
"""

from __future__ import annotations

import json
import secrets
import tempfile
import threading
from collections import OrderedDict
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import chatcard
from .chat import ChatEngine
from .funding import funding_view

MAX_BODY = 64 * 1024
MAX_MESSAGE = 2_000
MAX_CARDS = 32
MAX_TAPE_ROWS = 24
DEPLOYMENT_PATH = Path(__file__).resolve().parents[1] / "deployments" / "testnet.json"


def load_deployment() -> dict:
    """The testnet deployment record, or {} when there is not one.

    Kept as a plain dict for the page: the address and tx hashes are the only
    claim a visitor can check without trusting this repository.
    """
    try:
        data = json.loads(DEPLOYMENT_PATH.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    deploy = data.get("deploy") if isinstance(data.get("deploy"), dict) else {}
    publish = data.get("publish") if isinstance(data.get("publish"), dict) else {}
    return {
        "network": data.get("network", ""),
        "chainId": data.get("chainId"),
        "address": data.get("address", ""),
        "publisher": data.get("publisher", ""),
        "deployTx": deploy.get("tx", ""),
        "publishTx": publish.get("tx", ""),
    }


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HSK Perps · Trading Desk</title>
<style>
  :root{
    --bg:#070b10;--bg2:#0d1622;--panel:#111a24;--line:#1e2a38;--ink:#e8eef7;
    --muted:#8494a8;--green:#39d98a;--blue:#5aa9ff;--amber:#f5b84b;--red:#ff6b6b;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;color:var(--ink);font-family:var(--mono);
    background:
      radial-gradient(900px 500px at 12% -10%,rgba(90,169,255,.13),transparent 60%),
      radial-gradient(700px 420px at 92% 8%,rgba(57,217,138,.10),transparent 60%),
      linear-gradient(145deg,var(--bg),var(--bg2));
  }
  body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.35;
    background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),
                     linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);
    background-size:60px 60px;mask-image:linear-gradient(#000,transparent 85%)}
  .wrap{max-width:1240px;margin:0 auto;padding:28px 22px 36px;position:relative}
  header{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin-bottom:18px}
  .brand h1{font-size:22px;margin:0;letter-spacing:-.4px}
  .brand p{margin:6px 0 0;color:var(--muted);font-size:12.5px}
  .rule{height:5px;border-radius:3px;background:linear-gradient(90deg,var(--green),var(--blue),#a78bfa);margin:0 0 18px}
  .status{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;
    border:1px solid var(--line);background:rgba(17,26,36,.7);padding:8px 12px;border-radius:999px}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 12px var(--green)}
  .grid{display:grid;grid-template-columns:minmax(0,1.12fr) minmax(0,1fr);gap:18px}
  .panel{background:rgba(17,26,36,.78);border:1px solid var(--line);border-radius:18px;
    box-shadow:0 24px 60px rgba(0,0,0,.35);overflow:hidden;backdrop-filter:blur(8px)}
  .panel-h{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;
    border-bottom:1px solid var(--line);color:var(--muted);font-size:11px;letter-spacing:1.6px}
  .log{height:min(52vh,480px);overflow:auto;padding:16px;scroll-behavior:smooth}
  .msg{display:flex;gap:10px;margin-bottom:14px;align-items:flex-start}
  .msg .who{flex:0 0 44px;color:var(--muted);font-size:10px;letter-spacing:1.2px;padding-top:5px}
  .bubble{white-space:pre-wrap;line-height:1.5;font-size:13.5px;padding:11px 13px;border-radius:12px;
    border:1px solid var(--line);background:#0c141d;max-width:calc(100% - 56px);overflow-wrap:anywhere}
  .msg.me .bubble{background:#10202f;border-color:#21415c}
  .msg.desk .bubble{border-left:3px solid var(--blue)}
  .meta{margin-top:7px;display:flex;flex-wrap:wrap;gap:6px}
  .tag{font-size:10px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:2px 8px}
  .tag b{color:var(--ink);font-weight:600}
  .chips{display:flex;gap:8px;flex-wrap:wrap;padding:12px 16px 0}
  .chip{cursor:pointer;font:inherit;font-size:11.5px;color:var(--ink);background:#0d1722;
    border:1px solid var(--line);border-radius:999px;padding:7px 11px;transition:.15s}
  .chip:hover{border-color:var(--blue);transform:translateY(-1px)}
  form{display:flex;gap:10px;padding:12px 16px 16px}
  input{flex:1;background:#0a1119;border:1px solid var(--line);border-radius:12px;color:var(--ink);
    font:inherit;font-size:13.5px;padding:12px 14px;outline:none}
  input:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(90,169,255,.12)}
  button.send{font:inherit;font-size:12.5px;font-weight:700;color:#04120b;background:var(--green);
    border:0;border-radius:12px;padding:0 18px;cursor:pointer}
  button.send:disabled{opacity:.45;cursor:wait}
  .cardwrap{padding:14px}
  .card{width:100%;border-radius:12px;border:1px solid var(--line);display:block;background:#0a1119;min-height:210px}
  .empty{display:grid;place-items:center;height:100%;min-height:210px;color:var(--muted);font-size:12.5px;text-align:center;padding:20px}
  .actions{display:flex;gap:8px;padding:0 14px 14px;flex-wrap:wrap}
  .link{color:var(--blue);text-decoration:none;font-size:11.5px;border:1px solid var(--line);
    border-radius:9px;padding:7px 10px}
  .link:hover{border-color:var(--blue)}
  .hint{color:var(--muted);font-size:11px;line-height:1.6;padding:0 16px 16px}
  .kbd{border:1px solid var(--line);border-radius:5px;padding:1px 5px;color:var(--ink)}

  /* market tape */
  .tape{margin-bottom:18px}
  .tape .panel-h span:last-child{letter-spacing:.4px;text-transform:none}
  .kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:var(--line);gap:1px;
    border-bottom:1px solid var(--line)}
  .kpi{background:#0d1622;padding:11px 14px}
  .kpi span{display:block;color:var(--muted);font-size:9px;letter-spacing:1.5px;margin-bottom:5px}
  .kpi b{font-size:14.5px;font-weight:600;letter-spacing:-.2px}
  .kpi i{font-style:normal;color:var(--muted);font-size:10.5px;margin-left:6px}
  .tablewrap{overflow:auto;max-height:272px}
  table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}
  th{position:sticky;top:0;background:#0e1823;color:var(--muted);font-size:9px;letter-spacing:1.3px;
    font-weight:500;padding:8px 14px;text-align:right;border-bottom:1px solid var(--line);z-index:1}
  th:first-child,td:first-child{text-align:left}
  td{padding:7px 14px;text-align:right;border-bottom:1px solid rgba(30,42,56,.55);color:#c7d3e2}
  tbody tr{cursor:pointer;transition:background .12s}
  tbody tr:hover{background:#101d2a}
  td.mk{color:var(--ink);font-weight:600}
  td.dim{color:var(--muted)}
  .pos{color:var(--green)}
  .neg{color:var(--red)}
  .tapefoot{padding:8px 14px;color:var(--muted);font-size:10px;display:flex;justify-content:space-between;gap:10px}
  .tapefoot button{font:inherit;font-size:10px;color:var(--blue);background:none;border:0;cursor:pointer;padding:0}
  .badge{color:var(--green);border:1px solid rgba(57,217,138,.35);border-radius:999px;padding:3px 9px;
    font-size:9.5px;letter-spacing:.6px}
  @media (max-width:900px){.grid{grid-template-columns:1fr}.log{height:46vh}.kpis{grid-template-columns:repeat(2,1fr)}}
  @media (max-width:620px){.tablewrap{max-height:238px}th:nth-child(5),td:nth-child(5),
    th:nth-child(6),td:nth-child(6){display:none}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <h1>HSK Perps · Trading Desk</h1>
      <p>market tape + deterministic chat · read-only · venue chain 177 · SignalAdapter on testnet 133</p>
    </div>
    <div class="status"><span class="dot" id="dot"></span><span id="state">ready</span></div>
  </header>
  <div class="rule"></div>
  <section class="panel tape">
    <div class="panel-h">
      <span>MARKET TAPE · HSK PERPS</span>
      <span id="tape-meta">reading the venue…</span>
    </div>
    <div class="kpis">
      <div class="kpi"><span>BLOCK</span><b id="k-block">—</b></div>
      <div class="kpi"><span>MARKETS</span><b id="k-count">—</b></div>
      <div class="kpi"><span>WIDEST FUNDING</span><b id="k-top">—</b><i id="k-top-mk"></i></div>
      <div class="kpi"><span>CUM. VOLUME</span><b id="k-vol">—</b></div>
    </div>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th>MARKET</th><th>MARK</th><th>BASIS</th><th>FUNDING APR</th><th>OPEN INTEREST</th><th>CUM. VOLUME</th>
        </tr></thead>
        <tbody id="tape"><tr><td colspan="6" class="dim">loading…</td></tr></tbody>
      </table>
    </div>
    <div class="tapefoot">
      <span id="tape-note">—</span>
      <button id="tape-all" type="button">show all</button>
    </div>
  </section>
  <div class="grid">
    <section class="panel">
      <div class="panel-h"><span>DESK</span><span id="turns">0 turns</span></div>
      <div class="log" id="log">
        <div class="msg desk"><div class="who">DESK</div><div class="bubble">Ask for a scan, a sized plan, a venue precheck, or the HSK deploy gate.
Try <b>plan 1000 3x</b>.</div></div>
      </div>
      <div class="chips" id="chips"></div>
      <form id="form" autocomplete="off">
        <input id="q" placeholder="scan · plan $2,000 at 3x · check BTC · deploy" maxlength="2000">
        <button class="send" id="send" type="submit">send</button>
      </form>
    </section>
    <aside class="panel">
      <div class="panel-h"><span>TURN CARD</span><span>SVG · PNG</span></div>
      <div class="cardwrap" id="cardwrap">
        <div class="empty" id="empty">The card for the last answer appears here.<br>Every reply is reproducible from the same engine.</div>
      </div>
      <div class="actions" id="actions"></div>
      <div class="hint">
        <span class="kbd">Enter</span> sends · <span class="kbd">Shift</span>+<span class="kbd">Enter</span> newline.<br>
        The desk never signs, never places an order, and never asks for a private key.
      </div>
    </aside>
  </div>
</div>
<script>
const log = document.getElementById('log');
const form = document.getElementById('form');
const q = document.getElementById('q');
const send = document.getElementById('send');
const chips = document.getElementById('chips');
const cardwrap = document.getElementById('cardwrap');
const actions = document.getElementById('actions');
const state = document.getElementById('state');
const dot = document.getElementById('dot');
let turns = 0;

function bubble(who, text, tags) {
  const row = document.createElement('div');
  row.className = 'msg ' + (who === 'YOU' ? 'me' : 'desk');
  const w = document.createElement('div'); w.className = 'who'; w.textContent = who;
  const b = document.createElement('div'); b.className = 'bubble'; b.textContent = text;
  if (tags && tags.length) {
    const meta = document.createElement('div'); meta.className = 'meta';
    for (const t of tags) {
      const s = document.createElement('span'); s.className = 'tag';
      s.innerHTML = '<b></b> '; s.querySelector('b').textContent = t[0];
      s.appendChild(document.createTextNode(t[1]));
      meta.appendChild(s);
    }
    b.appendChild(meta);
  }
  row.append(w, b); log.appendChild(row); log.scrollTop = log.scrollHeight;
  return row;
}

function addChips(items) {
  chips.innerHTML = '';
  for (const text of items.slice(0, 5)) {
    const c = document.createElement('button');
    c.className = 'chip'; c.type = 'button'; c.textContent = text;
    c.onclick = () => { q.value = text; form.requestSubmit(); };
    chips.appendChild(c);
  }
}

async function ask(text) {
  if (!text.trim()) return;
  bubble('YOU', text);
  q.value = ''; send.disabled = true; state.textContent = 'thinking…'; dot.style.background = 'var(--amber)';
  const row = bubble('DESK', '…');
  const bubbleEl = row.querySelector('.bubble');
  try {
    const r = await fetch('/api/chat', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({message:text})});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    bubbleEl.textContent = data.reply;
    const tags = (data.metrics || []).map(m => [m.label, m.value]);
    if (tags.length) {
      const meta = document.createElement('div'); meta.className = 'meta';
      for (const [k, v] of tags) {
        const s = document.createElement('span'); s.className = 'tag';
        const b = document.createElement('b'); b.textContent = k + ': ';
        s.appendChild(b); s.appendChild(document.createTextNode(v)); meta.appendChild(s);
      }
      bubbleEl.appendChild(meta);
    }
    if (data.card_url) {
      cardwrap.innerHTML = '';
      const img = document.createElement('img'); img.className = 'card'; img.alt = 'answer card';
      img.src = data.card_url + '?v=' + Date.now(); cardwrap.appendChild(img);
      actions.innerHTML = '';
      for (const [href, label] of [[data.card_url, 'SVG'], [data.png_url, 'PNG']]) {
        if (!href) continue;
        const a = document.createElement('a'); a.className = 'link'; a.href = href; a.textContent = 'download ' + label;
        a.setAttribute('download', 'hsk-desk-card.' + label.toLowerCase()); actions.appendChild(a);
      }
    }
    addChips(data.suggestions || []);
    turns += 1; document.getElementById('turns').textContent = turns + (turns === 1 ? ' turn' : ' turns');
    state.textContent = 'ready'; dot.style.background = 'var(--green)';
  } catch (err) {
    bubbleEl.textContent = 'Desk error: ' + err.message;
    state.textContent = 'error'; dot.style.background = 'var(--red)';
  } finally {
    send.disabled = false; q.focus({preventScroll: true});
  }
}

const tape = document.getElementById('tape');
const tapeAll = document.getElementById('tape-all');
let tapeRows = [], tapeOpen = false;

const num = (v, d = 2) => Number(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
const money = (v) => {
  const n = Number(v);
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
  return '$' + n.toFixed(0);
};
const pct = (v) => (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
const short = (a) => a ? a.slice(0, 6) + '…' + a.slice(-4) : '—';

function renderTape() {
  const rows = tapeOpen ? tapeRows : tapeRows.slice(0, 8);
  tape.innerHTML = '';
  for (const m of rows) {
    const tr = document.createElement('tr');
    tr.title = 'ask funding ' + m.base;
    tr.onclick = () => ask('funding ' + m.base);
    const cells = [
      [m.name, 'mk'],
      [num(m.mark, 4), ''],
      [(m.basisBps >= 0 ? '+' : '') + Number(m.basisBps).toFixed(2) + ' bps', m.basisBps >= 0 ? 'pos' : 'neg'],
      [pct(m.fundingApr), m.fundingApr >= 0 ? 'pos' : 'neg'],
      [num(m.openInterest, 2), 'dim'],
      [money(m.volume), 'dim'],
    ];
    for (const [text, cls] of cells) {
      const td = document.createElement('td');
      td.textContent = text;
      if (cls) td.className = cls;
      tr.appendChild(td);
    }
    tape.appendChild(tr);
  }
  tapeAll.textContent = tapeOpen ? 'top 8' : 'all ' + tapeRows.length;
  tapeAll.hidden = tapeRows.length <= 8;
}

async function loadMarkets(force) {
  try {
    const r = await fetch('/api/markets' + (force ? '?force=1' : ''));
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
    tapeRows = d.markets || [];
    const total = tapeRows.reduce((a, m) => a + Number(m.volume), 0);
    const top = tapeRows[0] || null;
    document.getElementById('k-block').textContent = Number(d.block).toLocaleString('en-US');
    document.getElementById('k-count').textContent = d.counts.perps + ' / ' + d.counts.spots;
    document.getElementById('k-top').textContent = top ? pct(top.fundingApr) : '—';
    document.getElementById('k-top').className = top && top.fundingApr < 0 ? 'neg' : 'pos';
    document.getElementById('k-top-mk').textContent = top ? top.name.replace('-PERP', '') : '';
    document.getElementById('k-vol').textContent = money(total);
    document.getElementById('tape-meta').textContent = d.source + ' snapshot · ' + d.at;
    const dep = d.deployment || {};
    document.getElementById('tape-note').textContent = dep.address
      ? 'SignalAdapter ' + short(dep.address) + ' · chain ' + dep.chainId + ' ' + (dep.network || '')
      : 'no deployment record';
    renderTape();
  } catch (err) {
    tape.innerHTML = '';
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 6; td.className = 'dim'; td.textContent = 'tape unavailable: ' + err.message;
    tr.appendChild(td); tape.appendChild(tr);
    document.getElementById('tape-meta').textContent = 'offline';
  }
}

tapeAll.onclick = () => { tapeOpen = !tapeOpen; renderTape(); };
loadMarkets(false);
const preset = new URLSearchParams(location.search).get('ask');
if (preset) ask(preset);

form.addEventListener('submit', e => { e.preventDefault(); ask(q.value); });
q.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});
addChips(['scan','plan 1000 3x','check BTC 1000 3x','deploy','card']);
fetch('/api/health').then(r => r.json()).then(d => { state.textContent = 'ready · ' + d.version; }).catch(() => {});
</script>
</body>
</html>
"""


class Desk:
    """State for one local desk: the engine, plus the last N cards."""

    def __init__(self, engine: ChatEngine):
        self.engine = engine
        self.cards: OrderedDict[str, dict] = OrderedDict()
        self.lock = threading.Lock()

    def ask(self, message: str) -> dict:
        reply = self.engine.reply(message)
        snap = self.engine.snapshot() if self.engine._snapshot is not None else None
        block = snap.block if snap else None
        source = "indexer · live" if snap and snap.source == "live" else "fixture snapshot"
        svg = chatcard.render(reply, block=block, source=source)
        card_id = secrets.token_urlsafe(9)
        with self.lock:
            self.cards[card_id] = {
                "reply": reply,
                "svg": svg,
                "block": block,
                "source": source,
            }
            while len(self.cards) > MAX_CARDS:
                self.cards.popitem(last=False)
        payload = reply.as_dict()
        payload.update(
            {
                "card_id": card_id,
                "card_url": f"/api/card/{card_id}.svg",
                "png_url": f"/api/card/{card_id}.png",
            }
        )
        return payload

    def card(self, card_id: str) -> dict | None:
        with self.lock:
            return self.cards.get(card_id)

    def markets(self, refresh: bool = False) -> dict:
        """The tape the page renders. Same snapshot the chat answers from."""
        snap = self.engine.snapshot(refresh=refresh)
        rows = []
        for market in snap.perps:
            view = funding_view(market)
            rows.append(
                {
                    "base": market.base,
                    "name": market.display,
                    "mark": str(market.mark),
                    "index": str(market.index),
                    "basisBps": float(market.basis * 10_000),
                    "fundingApr": float(view.apr_percent),
                    "openInterest": float(market.open_interest),
                    "volume": float(market.volume),
                    "trades": market.trades,
                    "status": market.perp_status or "unknown",
                }
            )
        rows.sort(key=lambda row: (-abs(row["fundingApr"]), -row["volume"]))
        return {
            "ok": True,
            "block": snap.block,
            "at": snap.at,
            "source": snap.source or "snapshot",
            "counts": {"perps": len(snap.perps), "spots": len(snap.spots)},
            "markets": rows[:MAX_TAPE_ROWS],
            "truncated": len(rows) > MAX_TAPE_ROWS,
            "deployment": load_deployment(),
        }

    def png(self, card_id: str) -> bytes | None:
        item = self.card(card_id)
        if item is None:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.png"
            chatcard.write_png(item["reply"], path, block=item["block"], source=item["source"])
            return path.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "HSKDesk/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def desk(self) -> Desk:
        return self.server.desk  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # noqa: A003 - silence per-request access logs
        return

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # A client that sent a too-large body and closed early is not a
                # server error; the status line already went out.
                return

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            return
        if path == "/api/health":
            deployment = load_deployment()
            self._json(
                200,
                {
                    "ok": True,
                    "version": "1.1",
                    "cards": len(self.desk.cards),
                    "deployment": {
                        "chainId": deployment.get("chainId"),
                        "address": deployment.get("address", ""),
                    },
                },
            )
            return
        if path == "/api/markets":
            query = parse_qs(urlparse(self.path).query)
            refresh = query.get("force", ["0"])[0] == "1"
            self._json(200, self.desk.markets(refresh=refresh))
            return
        if path.startswith("/api/card/") and path.endswith(".svg"):
            card_id = path[len("/api/card/") : -4]
            item = self.desk.card(card_id)
            if item is None:
                self._json(404, {"error": "card not found"})
                return
            self._send(200, item["svg"].encode(), "image/svg+xml; charset=utf-8")
            return
        if path.startswith("/api/card/") and path.endswith(".png"):
            card_id = path[len("/api/card/") : -4]
            body = self.desk.png(card_id)
            if body is None:
                self._json(404, {"error": "card not found"})
                return
            self._send(200, body, "image/png")
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/chat":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            self._json(400, {"error": "empty body"})
            return
        if length > MAX_BODY:
            self._json(413, {"error": "body too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "invalid json"})
            return
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, str) or not message.strip():
            self._json(400, {"error": "message must be a non-empty string"})
            return
        if len(message) > MAX_MESSAGE:
            self._json(400, {"error": f"message longer than {MAX_MESSAGE} characters"})
            return
        self._json(200, self.desk.ask(message.strip()))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, desk: Desk):
        super().__init__(address, Handler)
        self.desk = desk

    def handle_error(self, request, client_address):
        # A client that closes after a 413 is not a server fault. Keep the test
        # runner's output clean without hiding real handler errors.
        import sys

        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def make_server(engine: ChatEngine, host: str = "127.0.0.1", port: int = 0) -> Server:
    return Server((host, port), Desk(engine))


def serve(engine: ChatEngine, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> int:
    server = make_server(engine, host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"HSK desk listening on {url}")
    print("  routes: /  POST /api/chat  GET /api/markets  GET /api/card/<id>.svg|.png  GET /api/health")
    print("  Ctrl-C to stop.")
    if open_browser:
        import webbrowser

        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
