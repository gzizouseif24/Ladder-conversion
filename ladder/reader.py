"""Read Mitsubishi instruction lists.

Supported inputs:
  * plain text, one instruction per line:   "LD X000"  or  "0  LD X000"  or  "0000 OUT T0 K50"
  * GX Developer / GX Works "Write to CSV" exports (comma or tab separated, UTF-8/UTF-16/Shift-JIS).
    In those exports, extra operands of an instruction come on the following rows with an
    empty instruction column; they are merged back into one instruction.
"""
from __future__ import annotations

import csv
import io
import re
from typing import List

from .model import Instr

_STEP_RE = re.compile(r"^\d+$")


def decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def looks_like_csv(text: str) -> bool:
    head = text.splitlines()[:10]
    return sum(1 for l in head if l.count('"') >= 4 or "\t" in l or l.count(",") >= 2) >= 2


def read_file(path: str) -> List[Instr]:
    with open(path, "rb") as f:
        text = decode(f.read())
    if path.lower().endswith((".csv", ".tsv")) or looks_like_csv(text):
        return parse_csv(text)
    return parse_text(text)


def parse_text(text: str) -> List[Instr]:
    out: List[Instr] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = re.split(r";|//", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        toks = line.split()
        step = None
        if _STEP_RE.match(toks[0]):
            step = int(toks[0])
            toks = toks[1:]
            if not toks:
                continue
        out.append(Instr(toks[0].upper(), toks[1:], step, lineno))
    return out


_INSTR_HEADERS = ("instruction", "命令")
_DEVICE_HEADERS = ("i/o", "device", "デバイス")
_STEP_HEADERS = ("step", "ステップ")


def parse_csv(text: str) -> List[Instr]:
    delim = "\t" if text.count("\t") > text.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))

    # defaults for GX exports: Step No., Line Statement, Instruction, I/O(Device), ...
    c_step, c_ins, c_dev = 0, 2, 3
    start = 0
    for i, row in enumerate(rows[:20]):
        low = [c.strip().lower() for c in row]
        ins = [j for j, c in enumerate(low) if any(h in c for h in _INSTR_HEADERS)]
        if ins:
            c_ins = ins[0]
            dev = [j for j, c in enumerate(low) if any(h in c for h in _DEVICE_HEADERS)]
            stp = [j for j, c in enumerate(low) if any(h in c for h in _STEP_HEADERS)]
            c_dev = dev[0] if dev else c_ins + 1
            c_step = stp[0] if stp else 0
            start = i + 1
            break

    def cell(row, j):
        return row[j].strip() if j < len(row) else ""

    out: List[Instr] = []
    for lineno, row in enumerate(rows[start:], start + 1):
        ins, dev, stp = cell(row, c_ins), cell(row, c_dev), cell(row, c_step)
        if ins:
            step = int(stp) if _STEP_RE.match(stp) else None
            if step is None and stp and not out:
                continue  # stray header/title row before the program
            out.append(Instr(ins.upper(), [dev] if dev else [], step, lineno))
        elif dev and out:
            out[-1].args.append(dev)
    return out
