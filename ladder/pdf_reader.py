"""Read an instruction list from a printed PDF (vendor software "Print" -> PDF).

The PDF must have a text layer (printed to PDF, not scanned). Every line is scanned for
entries of the form

    <step>  <mnemonic>  <operands>  [comment words]

Page headers, footers, titles and comments are skipped. A line holding only operands continues
the previous instruction (GX and FPWIN print extra operands of MOV, F0 MV, timers ... on their
own lines). Lines may hold several entries (multi-column prints); entries are put back in step
order.

Dialect is detected from the text: Mitsubishi (LD/OUT/...) or Panasonic (ST/OT/...).
"""
from __future__ import annotations

import re
from typing import List, Tuple

from . import dialects
from .builder import CONTACT_OPS, IGNORED, UNCONDITIONAL, _CMP_RE
from .model import Instr

KNOWN_OPS = set(CONTACT_OPS) | IGNORED | UNCONDITIONAL | dialects.PANASONIC_OPS | {
    "ANB", "ORB", "MPS", "MRD", "MPP", "INV", "MEP", "MEF", "STL",
    "OUT", "SET", "RST", "PLS", "PLF", "MC", "MCR", "CJ", "CALL", "FOR", "ZRST",
    "MOV", "MOVP", "DMOV", "DMOVP", "BMOV", "FMOV", "CMP", "DCMP", "ZCP", "ADD", "SUB", "MUL",
    "DIV", "INC", "DEC", "INCP", "DECP", "WAND", "WOR", "WXOR", "BCD", "BIN", "SFTL", "SFTR",
    "ROR", "ROL", "ALT", "ALTP", "FROM", "TO", "REF", "SPD", "PLSY", "PWM", "DECO", "ENCO",
    "FLT", "INT", "TRD", "TWR", "OUTH", "SEGD", "SEGL", "ABSD", "INCD", "RAMP", "SORT",
}

_DEVICE_RE = re.compile(
    r"""^(
        [KkHhEe]-?[0-9A-Fa-f.]+           # constants K10, H0FF, E1.5
      | [A-Z]{1,3}\d[0-9A-F]*(\.[0-9A-F])? # X000, D100, SM400, D0.F
      | U[0-9A-F]+\\G\d+                   # U0\\G10
      | K\d+[A-Z]+\d+                      # K4M0
      | "[^"]*"                            # string constant
    )$""", re.X)
_OP_RE = re.compile(r"^[A-Z]{2,7}/?$")
_STEP_RE = re.compile(r"^\d{1,6}$")


def is_device(tok: str) -> bool:
    return bool(_DEVICE_RE.match(tok))


def is_op(tok: str, strict: bool) -> bool:
    tok = tok.upper()
    if tok in KNOWN_OPS or _CMP_RE.match(tok):
        return True
    return not strict and bool(_OP_RE.match(tok))


def extract_text(path: str) -> List[str]:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise SystemExit("PDF input needs pypdf:  pip install pypdf") from e
    pages = [page.extract_text() or "" for page in PdfReader(path).pages]
    text = "\n".join(pages)
    if not text.strip():
        raise SystemExit(f"{path}: no text found - the PDF is probably a scan/image. "
                         "Print it to PDF from the software instead, or OCR it first.")
    return text.splitlines()


def _split_mitsubishi(toks: List[str]):
    op = toks[0].upper()
    args, i = [], 1
    while i < len(toks) and is_device(toks[i]):
        args.append(toks[i])
        i += 1
    return op, args, " ".join(toks[i:])


def parse_lines(lines: List[str], dialect: str = None) -> Tuple[List[Instr], List[str]]:
    """Return (instructions, skipped lines)."""
    dialect = dialect or dialects.detect("\n".join(lines))
    pan = dialect == "panasonic"
    found: List[Instr] = []
    skipped: List[str] = []
    last_step = -1

    for lineno, raw in enumerate(lines, 1):
        toks = raw.split()
        if not toks:
            continue

        if pan:
            # Panasonic prints one entry per line: "<step> <op> <operands>"
            if _STEP_RE.match(toks[0]) and len(toks) > 1 and is_op(toks[1], strict=int(toks[0]) <= last_step):
                op, args, comment = dialects.split_panasonic(toks[1:])
                found.append(Instr(op, args, int(toks[0]), lineno, comment))
                last_step = max(last_step, int(toks[0]))
            elif found and dialects.is_operand_line(toks):
                found[-1].args.extend(dialects.join_operands(toks)[0])
            else:
                skipped.append(raw.strip())
            continue

        entries: List[Instr] = []

        def starts_entry(j):
            if j + 1 >= len(toks) or not _STEP_RE.match(toks[j]):
                return False
            return is_op(toks[j + 1], strict=int(toks[j]) <= last_step)

        i = 0
        while i < len(toks) and starts_entry(i):
            j = i + 2
            while j < len(toks) and not starts_entry(j):
                j += 1
            op, args, comment = _split_mitsubishi(toks[i + 1:j])
            entries.append(Instr(op, args, int(toks[i]), lineno, comment))
            last_step = max(last_step, int(toks[i]))
            i = j
        if entries:
            found.extend(entries)
        elif found and all(is_device(t) for t in toks):
            found[-1].args.extend(toks)  # operand continuation line
        elif toks[0].upper() in KNOWN_OPS and all(is_device(t) for t in toks[1:]):
            found.append(Instr(toks[0].upper(), toks[1:], None, lineno))  # list without steps
        else:
            skipped.append(raw.strip())

    # multi-column pages interleave entries: restore step order, drop repeated steps
    if found and all(ins.step is not None for ins in found):
        seen, ordered = set(), []
        for ins in sorted(found, key=lambda x: x.step):
            if ins.step not in seen:
                seen.add(ins.step)
                ordered.append(ins)
        found = ordered
    return found, skipped


def read_pdf(path: str) -> List[Instr]:
    instrs, _ = parse_lines(extract_text(path))
    return instrs
