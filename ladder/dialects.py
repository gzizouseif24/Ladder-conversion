"""Instruction-set dialects.

Every dialect is mapped onto one canonical set (the Mitsubishi names) that builder.py
understands. The original mnemonic is kept for display.

  Mitsubishi (GX Developer / GX Works):  LD, LDI, AND, ANI, OR, ORI, ANB, ORB, MPS/MRD/MPP, OUT ...
  Panasonic  (FPWIN GR / FPWIN Pro):     ST, ST/, AN, AN/, OR, OR/, ANS, ORS, PSHS/RDS/POPS, DF, OT, ED ...
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

PANASONIC_ALIASES = {
    "ST": "LD", "ST/": "LDI", "AN": "AND", "AN/": "ANI", "OR/": "ORI",
    "ANS": "ANB", "ORS": "ORB", "PSHS": "MPS", "RDS": "MRD", "POPS": "MPP",
    "DF": "MEP", "DF/": "MEF", "DFI": "MEP", "NOT": "INV", "OT": "OUT", "ED": "END",
}
PANASONIC_OPS = set(PANASONIC_ALIASES) | {
    "OR", "SET", "RST", "KP", "NOP", "CNDE", "TML", "TMR", "TMX", "TMY", "CT", "SR",
    "MC", "MCE", "JP", "LBL", "LOOP", "CALL", "FCAL", "SUB", "RET", "INT", "IRET",
    "SSTP", "NSTP", "NSTL", "CSTP", "STPE", "SYS1", "SYS2", "F", "P", "P13",
}

# Device prefixes; their address may be pure hex ("R F", "H FFFF").
PANASONIC_DEVICES = {"X", "Y", "R", "L", "T", "C", "E", "H", "K", "DT", "LD", "FL", "WX", "WY",
                     "WR", "WL", "SV", "EV", "IX", "IY", "WI", "WO"}

# Instructions that take several stacked logic blocks as separate inputs.
MULTI_INPUT = {
    "KP": ["set", "reset"],
    "CT": ["count", "reset"],
    "SR": ["data", "shift", "reset"],
    "F118": ["up/down", "count", "reset"],
    "F119": ["left/right", "data", "shift", "reset"],
}

# Lines that name a section rather than act on logic: they label the next rung.
LABEL_OPS = {"LBL", "SUB", "INT", "SSTP"}

_PAN_LINE = re.compile(r"^\s*\d+\s+(ST/?|OT|AN/?|ED|PSHS|POPS|ORS|ANS|DF/?)(\s|$)", re.M)
_MIT_LINE = re.compile(r"^\s*(\d+\s+)?(LD[IPF]?|OUT|AND|ANI|ANB|ORB|MPS|MPP)(\s|$)", re.M)
CMP_SYMBOLS = ("=", "<>", ">=", "<=", ">", "<")
_NUM = re.compile(r"^-?[0-9A-F]+(\.[0-9]+)?$")
_LETTERS = re.compile(r"^[A-Z]{1,3}$")
_F_NUM = re.compile(r"^(\d+)(\(.*)?$")


def detect(text: str) -> str:
    return "panasonic" if len(_PAN_LINE.findall(text)) > len(_MIT_LINE.findall(text)) else "mitsubishi"


def canonical(op: str) -> str:
    """Canonical (Mitsubishi) name of an instruction, for the builder."""
    if op in PANASONIC_ALIASES:
        return PANASONIC_ALIASES[op]
    return op


def multi_input_key(op: str) -> str:
    """'F118(UDC)' -> 'F118', 'KP' -> 'KP'."""
    m = re.match(r"^(F\d+|P\d+)", op)
    return m.group(1) if m else op


def split_panasonic(toks: List[str]) -> Optional[Tuple[str, List[str], str]]:
    """Tokens after the step number -> (op, args, comment).

      ST R 9013        -> ('ST', ['R9013'])
      AN/ T 1005       -> ('AN/', ['T1005'])
      F 0 (MV)         -> ('F0(MV)', [])        operands follow on the next lines
      F 118(UDC)       -> ('F118(UDC)', [])
      ST >=  / ST =    -> ('ST>=', []) / ('ST=', [])
      STD>=            -> ('STD>=', [])
      TML 1003         -> ('TML', ['1003'])
    """
    if not toks:
        return None
    op, i = toks[0].upper(), 1
    if op in ("F", "P") and i < len(toks):
        m = _F_NUM.match(toks[i])
        if m:
            op += m.group(1)
            name = m.group(2) or ""
            i += 1
            if not name and i < len(toks) and toks[i].startswith("("):
                name = toks[i]
                i += 1
            while name and not name.endswith(")") and i < len(toks):
                name += toks[i]
                i += 1
            op += name
    elif i < len(toks) and toks[i] in CMP_SYMBOLS:
        op += toks[i]
        i += 1
    args, i = join_operands(toks, i)
    return op, args, " ".join(toks[i:])


def join_operands(toks: List[str], i: int = 0) -> Tuple[List[str], int]:
    """'R 9013' -> 'R9013', 'K 0' -> 'K0', bare numbers kept. Returns (args, next index)."""
    args = []
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if _LETTERS.match(t) and _NUM.match(nxt) and (
                t in PANASONIC_DEVICES or any(c.isdigit() for c in nxt)):
            args.append(t + toks[i + 1])
            i += 2
        elif _NUM.match(t) and any(c.isdigit() for c in t):
            args.append(t)
            i += 1
        else:
            break
    return args, i


def is_operand_line(toks: List[str]) -> bool:
    args, i = join_operands(toks)
    return bool(args) and i == len(toks) and not (len(toks) == 1)
