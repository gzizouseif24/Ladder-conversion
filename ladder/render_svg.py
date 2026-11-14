"""HTML page with one SVG drawing per rung.

All geometry comes from the grid in layout.py, so lines always meet exactly. Colours are CSS
variables, so the page follows light/dark mode.
"""
from __future__ import annotations

import html
from typing import List

from .layout import Layout

CW = 84        # cell width (px)
RH = 56        # row height (px)
PAD_X = 14     # left margin before the left rail
TOP = 32       # space above the first wire (for labels)
CHAR_W = 7.6   # approx. width of one monospace character at 12.5px
COIL_W = 64


def _names_width(o) -> float:
    return max((len(n) for n in o.input_names), default=0) * 6.4 + (12 if o.input_names else 0)


def _out_width(o) -> float:
    if o.inputs:
        return _names_width(o) + len(o.text) * CHAR_W + 22
    if o.is_coil:
        return max(COIL_W, len(o.text) * CHAR_W + 34)
    return len(o.text) * CHAR_W + 22


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def svg_rung(lay: Layout, out_w: float) -> str:
    X = lambda x: PAD_X + x * CW
    Y = lambda y: TOP + y * RH
    rail_x = X(lay.out_col) + out_w + 24
    width = rail_x + PAD_X
    height = TOP + (lay.height - 1) * RH + 22
    e = []
    line = lambda x1, y1, x2, y2, cls="w": e.append(
        f'<line class="{cls}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    text = lambda x, y, s, cls="lbl", anchor="middle": e.append(
        f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{_esc(s)}</text>')

    for p in lay.prims:
        kind = p[0]
        if kind == "hwire":
            _, x0, x1, y = p
            line(X(x0), Y(y), X(x1), Y(y))
        elif kind == "vwire":
            _, x, y0, y1 = p
            line(X(x), Y(y0), X(x), Y(y1))
        elif kind == "contact":
            _, x, y, c = p
            x0, x1, yy = X(x), X(x + c.width), Y(y)
            cx = (x0 + x1) / 2
            if c.kind in ("CMP", "BOX", "MEP", "MEF", "STL") or (c.kind == "INV" and c.text):
                label = c.text or c.kind
                bw = len(label) * CHAR_W + 16
                line(x0, yy, cx - bw / 2, yy)
                line(cx + bw / 2, yy, x1, yy)
                e.append(f'<rect class="box" x="{cx - bw / 2:.1f}" y="{yy - 12:.1f}" '
                         f'width="{bw:.1f}" height="24" rx="2"/>')
                text(cx, yy + 4.5, label, "box-t")
                if c.kind == "STL":
                    text(cx, yy - 17, c.device)
                continue
            if c.kind == "INV":
                line(x0, yy, x1, yy)
                line(cx - 6, yy + 10, cx + 6, yy - 10, "sym")
                continue
            line(x0, yy, cx - 8, yy)
            line(cx + 8, yy, x1, yy)
            line(cx - 8, yy - 13, cx - 8, yy + 13, "sym")
            line(cx + 8, yy - 13, cx + 8, yy + 13, "sym")
            if c.kind in ("NC", "PI", "FI"):
                line(cx - 12, yy + 11, cx + 12, yy - 11, "sym")
            if c.kind in ("P", "PI"):
                e.append(f'<path class="sym" d="M{cx:.1f} {yy + 8:.1f} V{yy - 8:.1f} '
                         f'M{cx - 4:.1f} {yy - 3:.1f} L{cx:.1f} {yy - 8:.1f} L{cx + 4:.1f} {yy - 3:.1f}"/>')
            if c.kind in ("F", "FI"):
                e.append(f'<path class="sym" d="M{cx:.1f} {yy - 8:.1f} V{yy + 8:.1f} '
                         f'M{cx - 4:.1f} {yy + 3:.1f} L{cx:.1f} {yy + 8:.1f} L{cx + 4:.1f} {yy + 3:.1f}"/>')
            text(cx, yy - 18, c.device)
        elif kind == "output":
            x, y, o = p[1], p[2], p[3]
            x0, yy = X(x), Y(y)
            ow = _out_width(o)
            bx = x0 + 12
            line(x0, yy, bx, yy)
            rows = p[4] if len(p) > 4 else None
            if rows:
                y_last = Y(rows[-1])
                for ry in rows[1:]:
                    line(x0, Y(ry), bx, Y(ry))
                    line(bx + ow, Y(ry), rail_x, Y(ry))
                e.append(f'<rect class="box" x="{bx:.1f}" y="{yy - 12:.1f}" width="{ow:.1f}" '
                         f'height="{y_last - yy + 24:.1f}" rx="2"/>')
                for k, ry in enumerate(rows):
                    if k < len(o.input_names):
                        text(bx + 6, Y(ry) + 4, o.input_names[k], "pin", "start")
                nw = _names_width(o)
                text(bx + nw + (ow - nw) / 2, (yy + y_last) / 2 + 4.5, o.text, "box-t")
                line(bx + ow, yy, rail_x, yy)
                continue
            if o.is_coil:
                r = 13
                c1, c2 = bx + 6, bx + ow - 6
                e.append(f'<path class="sym" d="M{c1 + 4:.1f} {yy - r:.1f} Q{c1 - 4:.1f} {yy:.1f} {c1 + 4:.1f} {yy + r:.1f}"/>')
                e.append(f'<path class="sym" d="M{c2 - 4:.1f} {yy - r:.1f} Q{c2 + 4:.1f} {yy:.1f} {c2 - 4:.1f} {yy + r:.1f}"/>')
                text(bx + ow / 2, yy + 4.5, o.text, "coil-t")
            else:
                e.append(f'<rect class="box" x="{bx:.1f}" y="{yy - 12:.1f}" width="{ow:.1f}" height="24" rx="2"/>')
                if o.op == "CALL" and o.args:
                    e.append(f'<a href="#blk-{_esc(o.args[0])}">')
                    text(bx + ow / 2, yy + 4.5, o.text, "box-t link")
                    e.append('</a>')
                else:
                    text(bx + ow / 2, yy + 4.5, o.text, "box-t")
            line(bx + ow, yy, rail_x, yy)

    line(X(0), 4, X(0), height - 4, "rail")
    line(rail_x, 4, rail_x, height - 4, "rail")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}" role="img">' + "".join(e) + "</svg>")


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1f23;--muted:#6b7078;--wire:#1d1f23;--sym:#1d1f23;
--rail:#1d1f23;--box:#eef3fb;--boxline:#3c63a8;--accent:#3c63a8;--warn:#a1500c;--line:#e3e3df}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15171a;--card:#1d2024;
--ink:#e7e8ea;--muted:#9aa0a8;--wire:#d6d8db;--sym:#e7e8ea;--rail:#d6d8db;--box:#22314b;
--boxline:#7ea3e6;--accent:#7ea3e6;--warn:#f0a35c;--line:#2c3035}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:20px 16px 8px;max-width:1200px;margin:auto}
h1{font-size:18px;margin:0 0 4px}
header p{margin:0;color:var(--muted)}
main{max-width:1200px;margin:auto;padding:8px 16px 40px}
.rung{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:10px 0;padding:10px 12px}
.head{display:flex;gap:12px;align-items:baseline;font-size:12.5px;color:var(--muted)}
.head b{color:var(--ink)}
.head .lbl{color:var(--accent);font-weight:600}
.scroll{overflow-x:auto}
svg{display:block;margin-top:4px}
svg .w{stroke:var(--wire);stroke-width:1.6}
svg .rail{stroke:var(--rail);stroke-width:3}
svg .sym{stroke:var(--sym);stroke-width:2;fill:none}
svg .box{fill:var(--box);stroke:var(--boxline);stroke-width:1.4}
svg text{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;fill:var(--ink)}
svg .box-t,svg .coil-t{font-weight:600}
svg .pin{font-size:10.5px;fill:var(--muted)}
details{margin-top:6px;font-size:12.5px;color:var(--muted)}
details pre{margin:6px 0 0;font:12.5px ui-monospace,Menlo,Consolas,monospace;color:var(--ink)}
.warn{color:var(--warn);font-size:12.5px;margin-top:4px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
nav a{font-size:12.5px;color:var(--muted);text-decoration:none;border:1px solid var(--line);
background:var(--card);border-radius:6px;padding:4px 8px}
nav a b{color:var(--ink)} nav .w{color:var(--warn)}
h2{font-size:16px;margin:26px 0 6px;scroll-margin-top:12px}
svg a .link{fill:var(--accent);text-decoration:underline}
"""


def _rung_html(lay: Layout, out_w: float) -> str:
    r = lay.rung
    if r.network:
        head = f'<b>{_esc(r.network)}</b>' if r.source else '<span>(continued)</span>'
    else:
        head = f'<b>Rung {r.index}</b>'
    if r.start_step is not None:
        head += f'<span>step {r.start_step}</span>'
    if r.label:
        head += f'<span class="lbl">{_esc(r.label)}</span>'
    warns = "".join(f'<div class="warn">⚠ {_esc(w)}</div>' for w in r.warnings)
    src = ""
    if r.source:
        lines = "\n".join(_esc(str(i)) for i in r.source)
        src = f'<details><summary>Instruction list</summary><pre>{lines}</pre></details>'
    return (f'<section class="rung"><div class="head">{head}</div>'
            f'<div class="scroll">{svg_rung(lay, out_w)}</div>{warns}{src}</section>')


def render_html(layouts: List[Layout], title: str = "Ladder") -> str:
    widths = {}  # output column width per block, so the right rail lines up within a block
    for l in layouts:
        for p in l.prims:
            if p[0] == "output":
                widths[l.rung.block] = max(widths.get(l.rung.block, COIL_W), _out_width(p[3]))
    n_warn = sum(len(l.rung.warnings) for l in layouts)
    body = []
    blocks = []  # (block, title, [layouts])
    for lay in layouts:
        b = lay.rung.block
        if not blocks or blocks[-1][0] != b:
            blocks.append((b, lay.rung.block_title or b, []))
        blocks[-1][2].append(lay)
    nav = ""
    if len(blocks) > 1 or blocks and blocks[0][0]:
        items = []
        for b, t, lays in blocks:
            nets = len({l.rung.network for l in lays})
            w = sum(len(l.rung.warnings) for l in lays)
            extra = f" · {nets} networks" if any(l.rung.network for l in lays) else f" · {len(lays)} rungs"
            extra += f' · <span class="w">{w} ⚠</span>' if w else ""
            items.append(f'<a href="#blk-{_esc(b)}"><b>{_esc(t)}</b>{extra}</a>')
        nav = '<nav>' + "".join(items) + '</nav>'
        for b, t, lays in blocks:
            body.append(f'<h2 id="blk-{_esc(b)}">{_esc(t)}</h2>')
            body.extend(_rung_html(l, widths.get(b, COIL_W)) for l in lays)
    else:
        body.extend(_rung_html(l, widths.get(l.rung.block, COIL_W)) for l in layouts)
    summary = (f"{len(blocks)} blocks · " if nav else "") + f"{len(layouts)} rungs" \
        + (f" · {n_warn} warnings" if n_warn else "")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_esc(title)}</title><style>{CSS}</style></head><body>'
            f'<header><h1>{_esc(title)}</h1><p>{summary}</p>{nav}</header><main>'
            + "".join(body) + "</main></body></html>")
