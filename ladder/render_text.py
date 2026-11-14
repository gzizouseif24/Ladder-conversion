"""Plain-text ladder, for the terminal and for tests.

Each grid row is two text lines: device labels, then the wire with symbols.

      X000      X001
  |---| |---+---|/|----------( Y000 )---|
"""
from __future__ import annotations

from typing import List

from .layout import Layout

CELL = 10

_SYMBOL = {
    "NO": "| |", "NC": "|/|", "P": "|P|", "F": "|F|", "PI": "|/P|", "FI": "|/F|",
    "INV": "/", "MEP": "[MEP]", "MEF": "[MEF]", "STL": "[STL]",
}


def _out_text(o) -> str:
    return f"( {o.text} )" if o.is_coil else f"[{o.text}]"


class _Canvas:
    def __init__(self, width, height):
        self.g = [[" "] * width for _ in range(height)]

    def put(self, col, line, s):
        for i, ch in enumerate(s):
            if 0 <= col + i < len(self.g[line]):
                self.g[line][col + i] = ch

    def get(self, col, line):
        return self.g[line][col]

    def text(self):
        return "\n".join("".join(r).rstrip() for r in self.g)


def render_rung(lay: Layout, out_width: int = None) -> str:
    outs = [p[3] for p in lay.prims if p[0] == "output"]
    out_width = out_width or max((len(_out_text(o)) for o in outs), default=4) + 2
    rail = lay.out_col * CELL + out_width + 2
    cv = _Canvas(rail + 1, lay.height * 2)

    def wl(y):  # wire line of row y
        return 2 * y + 1

    for p in lay.prims:
        if p[0] == "hwire":
            _, x0, x1, y = p
            cv.put(x0 * CELL, wl(y), "-" * ((x1 - x0) * CELL + 1))
        elif p[0] == "contact":
            _, x, y, c = p
            w = c.width * CELL
            sym = f"[{c.text}]" if c.kind == "CMP" else _SYMBOL.get(c.kind, "| |")
            cv.put(x * CELL, wl(y), "-" * (w + 1))
            cv.put(x * CELL + (w - len(sym)) // 2 + 1, wl(y), sym)
            label = c.device[: w - 2]
            cv.put(x * CELL + (w - len(label)) // 2 + 1, wl(y) - 1, label)
        elif p[0] == "output":
            _, x, y, o = p
            s = _out_text(o)
            col = x * CELL
            cv.put(col, wl(y), "-" * (rail - col))
            cv.put(col + 2, wl(y), s)
    for p in lay.prims:
        if p[0] == "vwire":
            _, x, y0, y1 = p
            col = x * CELL
            for line in range(wl(y0), wl(y1) + 1):
                cv.put(col, line, "+" if (line - 1) % 2 == 0 else "|")
    for line in range(lay.height * 2):
        cv.put(0, line, "|")
        cv.put(rail, line, "|")
    return cv.text()


def render(layouts: List[Layout]) -> str:
    outs = [p[3] for l in layouts for p in l.prims if p[0] == "output"]
    out_width = max((len(_out_text(o)) for o in outs), default=4) + 2
    parts = []
    for lay in layouts:
        r = lay.rung
        head = f"Rung {r.index}"
        if r.start_step is not None:
            head += f"  (step {r.start_step})"
        if r.label:
            head += f"  [{r.label}]"
        parts.append(head)
        parts.append(render_rung(lay, out_width))
        for w in r.warnings:
            parts.append(f"  ! {w}")
        parts.append("")
    return "\n".join(parts)
