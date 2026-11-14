"""Command line:  python -m ladder INPUT [-o OUT.html] [--text] [--list]

INPUT is a file (.pdf, .txt, .csv, .awl) or a folder of such files (one page for all of them).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List

from . import siemens
from .builder import build
from .layout import layout_all
from .model import Rung
from .reader import decode, read_file
from .render_svg import render_html
from .render_text import render as render_text

INPUT_EXT = (".pdf", ".txt", ".csv", ".tsv", ".awl", ".il", ".lst")


def _siemens_text(path: str):
    if path.lower().endswith(".pdf"):
        return None
    with open(path, "rb") as f:
        text = decode(f.read())
    return text if siemens.is_siemens(text) else None


def load(path: str) -> List[Rung]:
    text = _siemens_text(path)
    if text is not None:
        return siemens.convert_text(text)
    return build(read_file(path))


def listing(path: str) -> str:
    text = _siemens_text(path)
    if text is not None:
        return siemens.list_text(text)
    lines = []
    instrs = read_file(path)
    for ins in instrs:
        step = "" if ins.step is None else ins.step
        lines.append(f"{step:>6}  {ins}")
    lines.append(f"-- {len(instrs)} instructions")
    return "\n".join(lines)


def files_of(path: str) -> List[str]:
    if not os.path.isdir(path):
        return [path]
    return sorted(os.path.join(path, f) for f in os.listdir(path)
                  if f.lower().endswith(INPUT_EXT) and not f.startswith("."))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ladder", description="Convert PLC instruction lists to ladder diagrams.")
    ap.add_argument("input", help="file (.pdf .txt .csv .awl) or a folder of them")
    ap.add_argument("-o", "--output", help="HTML file to write (default: next to the input)")
    ap.add_argument("--text", action="store_true", help="print a text ladder to the terminal instead")
    ap.add_argument("--list", action="store_true",
                    help="only print the instructions read from the input (check what was read)")
    args = ap.parse_args(argv)

    files = files_of(args.input)
    if not files:
        print(f"no input files ({', '.join(INPUT_EXT)}) in {args.input}")
        return 1
    if args.list:
        for f in files:
            if len(files) > 1:
                print(f"\n##### {f}")
            print(listing(f))
        return 0

    rungs: List[Rung] = []
    for f in files:
        part = load(f)
        if len(files) > 1:
            name = os.path.splitext(os.path.basename(f))[0]
            for r in part:
                if not r.block:
                    r.block = r.block_title = name
                else:
                    r.block = f"{name}/{r.block}"
        rungs.extend(part)
    for n, r in enumerate(rungs):
        r.index = n

    layouts = layout_all(rungs)
    if args.text:
        sys.stdout.write(render_text(layouts))
        return 0
    if args.output:
        out = args.output
    elif os.path.isdir(args.input):
        out = os.path.join(args.input, os.path.basename(os.path.normpath(args.input)) + ".html")
    else:
        out = os.path.splitext(args.input)[0] + ".html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_html(layouts, os.path.basename(os.path.normpath(args.input))))
    warns = sum(len(r.warnings) for r in rungs)
    print(f"{len(rungs)} rungs -> {out}" + (f" ({warns} warnings)" if warns else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
