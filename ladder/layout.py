"""Place a rung on a grid, the same way GX Developer does.

Grid units: x = column (cell boundary), y = row. Every renderer (text, SVG) only maps these
grid coordinates to characters or pixels, so the geometry is decided once, here.

Primitives:
  ("contact", x, y, Contact)      occupies cells x .. x+contact.width
  ("hwire", x0, x1, y)            horizontal wire between cell boundaries
  ("vwire", x, y0, y1)            vertical wire on boundary x between rows
  ("output", x, y, Output[, rows]) output box starting at boundary x (= output column);
                                  multi-input boxes also carry the row of every input
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .model import Branch, Contact, Output, Parallel, Rung, Series


def expr_size(e) -> Tuple[int, int]:
    if e is None:
        return 0, 1
    if isinstance(e, Contact):
        return e.width, 1
    if isinstance(e, Series):
        sizes = [expr_size(i) for i in e.items]
        return sum(w for w, _ in sizes), max(h for _, h in sizes)
    if isinstance(e, Parallel):
        sizes = [expr_size(b) for b in e.branches]
        return max(w for w, _ in sizes), sum(h for _, h in sizes)
    raise TypeError(e)


def logic_width(br: Branch) -> int:
    """Columns needed before the output column."""
    w, _ = expr_size(br.expr)
    kids = [logic_width(c) if isinstance(c, Branch) else
            max((expr_size(e)[0] for e in c.inputs), default=0) for c in br.children]
    return w + max(kids, default=0)


@dataclass
class Layout:
    rung: Rung
    out_col: int
    height: int = 1
    prims: List[tuple] = field(default_factory=list)


def _place_expr(e, x, y, prims):
    if e is None:
        return
    if isinstance(e, Contact):
        prims.append(("contact", x, y, e))
    elif isinstance(e, Series):
        for item in e.items:
            _place_expr(item, x, y, prims)
            x += expr_size(item)[0]
    elif isinstance(e, Parallel):
        W, _ = expr_size(e)
        row = y
        last = y
        for b in e.branches:
            bw, bh = expr_size(b)
            _place_expr(b, x, row, prims)
            if bw < W:
                prims.append(("hwire", x + bw, x + W, row))
            last = row
            row += bh
        prims.append(("vwire", x, y, last))
        prims.append(("vwire", x + W, y, last))


def _place_branch(br: Branch, x, y, out_col, prims) -> int:
    w, h = expr_size(br.expr)
    _place_expr(br.expr, x, y, prims)
    cx = x + w
    row = y
    rows = []
    for ch in br.children:
        rows.append(row)
        if isinstance(ch, Output) and ch.inputs:
            in_rows = []
            for e in ch.inputs:
                ew, eh = expr_size(e)
                _place_expr(e, cx, row, prims)
                if cx + ew < out_col:
                    prims.append(("hwire", cx + ew, out_col, row))
                in_rows.append(row)
                row += eh
            prims.append(("output", out_col, in_rows[0], ch, in_rows))
        elif isinstance(ch, Output):
            if cx < out_col:
                prims.append(("hwire", cx, out_col, row))
            prims.append(("output", out_col, row, ch))
            row += 1
        else:
            row += _place_branch(ch, cx, row, out_col, prims)
    if len(rows) > 1:
        prims.append(("vwire", cx, y, rows[-1]))
    return max(h, row - y)


def layout(rung: Rung, out_col: int = None, min_out_col: int = 1) -> Layout:
    if out_col is None:
        out_col = max(logic_width(rung.root), min_out_col)
    lay = Layout(rung, out_col)
    lay.height = _place_branch(rung.root, 0, 0, out_col, lay.prims)
    return lay


def layout_all(rungs: List[Rung], align: bool = True) -> List[Layout]:
    """align=True puts every rung's outputs in the same column (like GX Developer)."""
    col = max((logic_width(r.root) for r in rungs), default=1) if align else None
    return [layout(r, col) for r in rungs]
