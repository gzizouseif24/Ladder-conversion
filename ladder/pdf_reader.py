"""Read an instruction list from a printed PDF (vendor software "Print" -> PDF).

The PDF must have a text layer (printed to PDF, not scanned). Text is extracted with its
layout kept, then every line is scanned for entries of the form

    <step>  <mnemonic>  <device> <device> ...  [comment words]

Page headers, footers, titles and comments are skipped. A line holding only devices continues
the previous instruction (GX prints extra operands of MOV, CMP, ... on their own lines).
Lines may hold several entries (multi-column prints); entries are put back in step order.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .builder import CONTACT_OPS, IGNORED, UNCONDITIONAL, _CMP_RE
from .model import Instr

KNOWN_OPS = set(CONTACT_OPS) | IGNORED | UNCONDITIONAL | {
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
_OP_RE = re.compile(r"^[A-Z]{2,7}$")
_STEP_RE = re.compile(r"^\d{1,6}$")


def is_device(tok: str) -> bool:
    return bool(_DEVICE_RE.match(tok))


def is_op(tok: str, strict: bool) -> bool:
    if tok in KNOWN_OPS or _CMP_RE.match(tok):
        return True
    return not strict and bool(_OP_RE.match(tok))


def extract_text(path: str) -> List[str]:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise SystemExit("PDF input needs pypdf:  pip install pypdf") from e
    pages = []
    for page in PdfReader(path).pages:
        try:
            pages.append(page.extract_text(extraction_mode="layout"))
        except TypeError:  # older pypdf
            pages.append(page.extract_text())
    text = "\n".join(p or "" for p in pages)
    if not text.strip():
        raise SystemExit(f"{path}: no text found - the PDF is probably a scan/image. "
                         "Print it to PDF from the software instead, or OCR it first.")
    return text.splitlines()


def parse_lines(lines: List[str]) -> Tuple[List[Instr], List[str]]:
    """Return (instructions, skipped lines)."""
    found: List[Instr] = []
    skipped: List[str] = []
    last_step = -1
    for lineno, raw in enumerate(lines, 1):
        toks = raw.split()
        if not toks:
            continue
        entries: List[Instr] = []

        def starts_entry(j):
            if j + 1 >= len(toks) or not _STEP_RE.match(toks[j]):
                return False
            return is_op(toks[j + 1].upper(), strict=int(toks[j]) <= last_step)

        i = 0
        while i < len(toks) and starts_entry(i):
            ins = Instr(toks[i + 1].upper(), [], int(toks[i]), lineno)
            last_step = max(last_step, ins.step)
            i += 2
            while i < len(toks) and is_device(toks[i]):
                ins.args.append(toks[i])
                i += 1
            words = []
            while i < len(toks) and not starts_entry(i):
                words.append(toks[i])
                i += 1
            ins.comment = " ".join(words)
            entries.append(ins)
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
