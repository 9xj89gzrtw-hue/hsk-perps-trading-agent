"""Chat cards: one self-contained SVG, and a PNG when a renderer is available.

The SVG is the source of truth: no fonts, no network, no JavaScript. The PNG is
for the places that cannot show an SVG (Telegram previews, a README, a chat
attachment). We draw the PNG directly with Pillow when it is installed, which
keeps the type crisp; without Pillow the module still writes a valid PNG with the
same palette and layout bars, so `--png` never silently produces nothing.
"""

from __future__ import annotations

import struct
import textwrap
import zlib
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .chat import Reply

W, H = 1200, 760
BG0 = (7, 11, 16)
BG1 = (13, 22, 34)
PANEL = "#111a24"
INK = "#e8eef7"
MUTED = "#8494a8"
GREEN = "#39d98a"
RED = "#ff6b6b"
AMBER = "#f5b84b"
BLUE = "#5aa9ff"

TONE = {"green": GREEN, "red": RED, "amber": AMBER, "blue": BLUE, "muted": MUTED}

WRAP = 92


def _wrap(text: str, width: int = WRAP, max_lines: int = 14) -> list[str]:
    out: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        if not paragraph:
            out.append("")
            continue
        out.extend(textwrap.wrap(paragraph, width=width, break_long_words=False, replace_whitespace=False) or [""])
    if len(out) > max_lines:
        out = out[: max_lines - 1] + ["…"]
    return out


def _wrap_question(text: str, width: int = 74, max_lines: int = 2) -> list[str]:
    return _wrap(text, width=width, max_lines=max_lines)


def render(reply: Reply, block: int | None = None, source: str = "", generated_at: str | None = None) -> str:
    """Render a 1200x760 SVG card for one chat turn."""
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tone = "red" if reply.intent == "error" else "blue"
    accent = TONE.get(tone, BLUE)
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">'
    )
    parts.append(
        '<defs>'
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="#{BG0[0]:02x}{BG0[1]:02x}{BG0[2]:02x}"/>'
        f'<stop offset="1" stop-color="#{BG1[0]:02x}{BG1[1]:02x}{BG1[2]:02x}"/>'
        '</linearGradient>'
        '<linearGradient id="rule" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#39d98a"/><stop offset="0.55" stop-color="#5aa9ff"/>'
        '<stop offset="1" stop-color="#a78bfa"/></linearGradient>'
        '</defs>'
    )
    parts.append(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
    # faint grid
    for x in range(0, W, 60):
        parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{H}" stroke="#ffffff" stroke-opacity="0.018"/>')
    for y in range(0, H, 60):
        parts.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#ffffff" stroke-opacity="0.018"/>')
    parts.append(f'<rect x="0" y="0" width="{W}" height="6" fill="url(#rule)"/>')

    parts.append(f'<text x="56" y="70" fill="{INK}" font-size="28" font-weight="700">HSK PERPS · conversational desk</text>')
    parts.append(
        f'<text x="56" y="98" fill="{MUTED}" font-size="14">chain 177 planning · read-only · '
        f'{escape(stamp)}</text>'
    )
    # intent pill
    label = reply.intent.upper()[:18]
    pill_w = max(96, 18 + len(label) * 11)
    parts.append(f'<rect x="{W - 56 - pill_w}" y="50" width="{pill_w}" height="34" rx="17" fill="{accent}" opacity="0.15"/>')
    parts.append(
        f'<text x="{W - 56 - pill_w / 2}" y="73" fill="{accent}" font-size="13" font-weight="700" '
        f'text-anchor="middle" letter-spacing="1.5">{escape(label)}</text>'
    )

    # question
    q_lines = _wrap_question(reply.question or "(no question)")
    parts.append(f'<text x="56" y="150" fill="{MUTED}" font-size="12" letter-spacing="2">YOU ASKED</text>')
    y = 180
    for line in q_lines:
        parts.append(f'<text x="56" y="{y}" fill="{INK}" font-size="19" opacity="0.92">{escape(line)}</text>')
        y += 28

    # answer panel
    lines = _wrap(reply.text, max_lines=11)
    panel_h = min(392, 92 + 28 * max(1, len(lines)))
    panel_y = y + 18
    parts.append(f'<rect x="40" y="{panel_y}" width="{W - 80}" height="{panel_h}" rx="16" fill="{PANEL}" stroke="#1e2a38"/>')
    parts.append(f'<rect x="40" y="{panel_y}" width="4" height="{panel_h}" rx="2" fill="{accent}"/>')
    parts.append(f'<text x="72" y="{panel_y + 34}" fill="{MUTED}" font-size="12" letter-spacing="2">DESK</text>')
    ty = panel_y + 70
    for line in lines:
        if line:
            parts.append(f'<text x="72" y="{ty}" fill="{INK}" font-size="17">{escape(line)}</text>')
        ty += 28

    # metrics, directly under the answer panel (and never over the footer)
    metrics = list(reply.metrics)[:4]
    if metrics:
        gap = 16
        card_w = (W - 80 - gap * (len(metrics) - 1)) / len(metrics)
        my = panel_y + panel_h + 20
        for idx, (name, value, mtone) in enumerate(metrics):
            x = 40 + idx * (card_w + gap)
            color = TONE.get(mtone, BLUE)
            parts.append(f'<rect x="{x:.0f}" y="{my}" width="{card_w:.0f}" height="84" rx="12" fill="{PANEL}" stroke="#1e2a38"/>')
            parts.append(f'<text x="{x + 18:.0f}" y="{my + 27}" fill="{MUTED}" font-size="11" letter-spacing="1.5">{escape(name.upper())}</text>')
            parts.append(f'<text x="{x + 18:.0f}" y="{my + 58}" fill="{color}" font-size="19" font-weight="700">{escape(str(value))}</text>')

    footer = f"block {block:,} · {source}" if block else (source or "fixture snapshot")
    parts.append(f'<text x="56" y="726" fill="{MUTED}" font-size="12">{escape(footer)}</text>')
    parts.append(
        f'<text x="{W - 56}" y="726" fill="{MUTED}" font-size="12" text-anchor="end">'
        f'github.com/9xj89gzrtw-hue/hsk-perps-trading-agent</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def write(reply: Reply, path: str | Path, block: int | None = None, source: str = "") -> str:
    svg = render(reply, block=block, source=source)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(svg, encoding="utf-8")
    return svg


# --- PNG -------------------------------------------------------------------


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient(width: int, height: int):
    from PIL import Image

    image = Image.new("RGB", (width, height), BG0)
    px = image.load()
    for yy in range(height):
        t = yy / max(1, height - 1)
        # ease the diagonal just enough to read as light, not a CSS demo
        r = int(BG0[0] * (1 - t) + BG1[0] * t)
        g = int(BG0[1] * (1 - t) + BG1[1] * t)
        b = int(BG0[2] * (1 - t) + BG1[2] * t)
        for xx in range(width):
            d = (xx / max(1, width - 1)) * 0.35
            px[xx, yy] = (int(r * (1 - d) + 18 * d), int(g * (1 - d) + 30 * d), int(b * (1 - d) + 48 * d))
    return image


def _hex(rgb: str):
    rgb = rgb.lstrip("#")
    return tuple(int(rgb[i : i + 2], 16) for i in (0, 2, 4))


def write_png(reply: Reply, path: str | Path, block: int | None = None, source: str = "", width: int = W, height: int = H) -> str:
    """Write a PNG and return the renderer used: `pillow` or `minimal`."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001 - Pillow is optional
        _minimal_png(Path(path), width, height)
        return "minimal"

    image = _gradient(width, height)
    draw = ImageDraw.Draw(image)
    accent_hex = RED if reply.intent == "error" else BLUE
    accent = _hex(accent_hex)

    # rule
    for x in range(width):
        t = x / max(1, width - 1)
        color = (int(57 + (90 - 57) * t), int(217 + (169 - 217) * t), int(138 + (255 - 138) * t))
        draw.line([(x, 0), (x, 5)], fill=color)

    f_title, f_sub, f_label = _font(30), _font(14), _font(12)
    f_body, f_small, f_metric = _font(18), _font(12), _font(20)
    draw.text((56, 42), "HSK PERPS · conversational desk", font=f_title, fill=_hex(INK))
    draw.text((56, 84), f"chain 177 planning · read-only · {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}", font=f_sub, fill=_hex(MUTED))

    label_txt = reply.intent.upper()[:18]
    tw = draw.textlength(label_txt, font=f_small)
    pill_w = max(96, int(tw) + 34)
    draw.rounded_rectangle([width - 56 - pill_w, 46, width - 56, 80], radius=17, fill=_hex("#16222f"))
    draw.text((width - 56 - pill_w / 2 - tw / 2, 55), label_txt, font=f_small, fill=accent)

    draw.text((56, 138), "YOU ASKED", font=f_label, fill=_hex(MUTED))
    y = 164
    for line in _wrap_question(reply.question or "(no question)"):
        draw.text((56, y), line, font=f_body, fill=_hex(INK))
        y += 26

    lines = _wrap(reply.text, max_lines=11)
    panel_h = min(392, 92 + 28 * max(1, len(lines)))
    panel_y = y + 16
    draw.rounded_rectangle([40, panel_y, width - 40, panel_y + panel_h], radius=16, fill=_hex(PANEL), outline=_hex("#1e2a38"))
    draw.rounded_rectangle([40, panel_y, 44, panel_y + panel_h], radius=2, fill=accent)
    draw.text((72, panel_y + 20), "DESK", font=f_label, fill=_hex(MUTED))
    ty = panel_y + 54
    for line in lines:
        if line:
            draw.text((72, ty), line, font=f_body, fill=_hex(INK))
        ty += 27

    metrics = list(reply.metrics)[:4]
    if metrics:
        gap = 16
        card_w = int((width - 80 - gap * (len(metrics) - 1)) / len(metrics))
        my = panel_y + panel_h + 20
        for idx, (name, value, mtone) in enumerate(metrics):
            x = 40 + idx * (card_w + gap)
            color = _hex(TONE.get(mtone, BLUE))
            draw.rounded_rectangle([x, my, x + card_w, my + 84], radius=12, fill=_hex(PANEL), outline=_hex("#1e2a38"))
            draw.text((x + 18, my + 16), name.upper(), font=f_label, fill=_hex(MUTED))
            draw.text((x + 18, my + 42), str(value), font=f_metric, fill=color)

    footer = f"block {block:,} · {source}" if block else (source or "fixture snapshot")
    draw.text((56, height - 38), footer, font=f_small, fill=_hex(MUTED))
    right = "github.com/9xj89gzrtw-hue/hsk-perps-trading-agent"
    rw = draw.textlength(right, font=f_small)
    draw.text((width - 56 - rw, height - 38), right, font=f_small, fill=_hex(MUTED))
    image.save(path, "PNG", optimize=True)
    return "pillow"


def _minimal_png(path: Path, width: int, height: int) -> None:
    """A valid PNG with the card palette when Pillow is unavailable (stdlib only)."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0
        t = y / max(1, height - 1)
        r = int(BG0[0] * (1 - t) + BG1[0] * t)
        g = int(BG0[1] * (1 - t) + BG1[1] * t)
        b = int(BG0[2] * (1 - t) + BG1[2] * t)
        for x in range(width):
            if y < 6:
                d = x / max(1, width - 1)
                rows += bytes((int(57 + (90 - 57) * d), int(217 + (169 - 217) * d), int(138 + (255 - 138) * d)))
            elif 606 <= y <= 690 and (40 <= x <= width - 40):
                rows += bytes((17, 26, 36))
            else:
                rows += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
