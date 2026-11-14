"""Command line:  python -m ladder INPUT [-o OUT.html] [--text]"""
from __future__ import annotations

import argparse
import os
import sys

from .builder import build
from .layout import layout_all
from .reader import read_file
from .render_svg import render_html
from .render_text import render as render_text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ladder", description="Convert a Mitsubishi instruction list to ladder.")
    ap.add_argument("input", help="instruction list (.txt) or GX Developer CSV export")
    ap.add_argument("-o", "--output", help="write an HTML page (default: <input>.html)")
    ap.add_argument("--text", action="store_true", help="print a text ladder to the terminal instead")
    args = ap.parse_args(argv)

    rungs = build(read_file(args.input))
    layouts = layout_all(rungs)
    if args.text:
        sys.stdout.write(render_text(layouts))
        return 0
    out = args.output or os.path.splitext(args.input)[0] + ".html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_html(layouts, os.path.basename(args.input)))
    warns = sum(len(r.warnings) for r in rungs)
    print(f"{len(rungs)} rungs -> {out}" + (f" ({warns} warnings)" if warns else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
