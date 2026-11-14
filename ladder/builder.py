"""Turn a Mitsubishi instruction list into ladder rungs.

Semantics (FX / A / Q series):
  LD/LDI/LDP/LDF ...   start a new logic block (pushed on the block stack)
  AND/ANI/...          contact in series with the current block
  OR/ORI/...           contact in parallel with the current block (everything since its LD)
  ANB / ORB            pop two blocks, join them in series / in parallel
  MPS / MRD / MPP      push / read / pop a branch point
  INV, MEP, MEF        operate on the current result
  anything else        an output or applied instruction (OUT, SET, RST, MOV, ...)
"""
from __future__ import annotations

import re
from typing import List, Optional

from .model import Branch, Contact, Instr, Output, Rung, parallel, series

_PULSE = {"": "NO", "I": "NC", "P": "P", "F": "F", "PI": "PI", "FI": "FI"}
CONTACT_OPS = {}
for _base, _kind in (("LD", "start"), ("AND", "and"), ("OR", "or")):
    for _suffix, _ckind in _PULSE.items():
        _op = _base + _suffix
        if _base == "AND" and _suffix == "I":
            _op = "ANI"
        CONTACT_OPS[_op] = (_kind, _ckind)

_CMP_RE = re.compile(r"^(LD|AND|OR)([DE]|DE)?(=|<>|>=|<=|>|<)$")

# Instructions that need no input condition and stand on their own rung.
UNCONDITIONAL = {"END", "FEND", "RET", "SRET", "IRET", "NEXT", "MCR", "EI", "DI", "BREAK"}
IGNORED = {"NOP", "NOPLF", "PAGE"}
POINTER_RE = re.compile(r"^[PI]\d+$")


def classify(ins: Instr):
    """Return (kind, contact) where kind is start/and/or or None."""
    if ins.op in CONTACT_OPS:
        kind, ckind = CONTACT_OPS[ins.op]
        return kind, Contact(ins.args[0] if ins.args else "?", ckind)
    m = _CMP_RE.match(ins.op)
    if m:
        kind = {"LD": "start", "AND": "and", "OR": "or"}[m.group(1)]
        text = (m.group(2) or "") + m.group(3) + " " + " ".join(ins.args)
        return kind, Contact("", "CMP", text.strip())
    if ins.op == "STL":
        return "start", Contact(ins.args[0] if ins.args else "?", "STL")
    return None, None


class _RungBuilder:
    def __init__(self, index: int, label: str = ""):
        self.root = Branch()
        self.anchor = self.root
        self.blocks: list = []
        self.mps: List[Branch] = []
        self.has_output = False
        self.rung = Rung(self.root, None, index, label)

    @property
    def empty(self) -> bool:
        return not self.rung.source

    def add_source(self, ins: Instr):
        if self.rung.start_step is None and not self.rung.source:
            self.rung.start_step = ins.step
        self.rung.source.append(ins)

    def warn(self, ins: Instr, msg: str):
        self.rung.warnings.append(f"line {ins.line} '{ins}': {msg}")

    def _pending(self, ins: Instr):
        if len(self.blocks) > 1:
            self.warn(ins, f"{len(self.blocks)} unjoined logic blocks (missing ANB/ORB?) - joined in series")
        cond = None
        for b in self.blocks:
            cond = series(cond, b)
        self.blocks = []
        return cond

    def _attach_pending(self, ins: Instr):
        cond = self._pending(ins)
        if cond is not None:
            node = Branch(cond)
            self.anchor.children.append(node)
            self.anchor = node

    def feed(self, ins: Instr):
        self.add_source(ins)
        op = ins.op
        kind, contact = classify(ins)
        if kind == "start":
            self.blocks.append(contact)
        elif kind == "and":
            if self.blocks:
                self.blocks[-1] = series(self.blocks[-1], contact)
            else:
                self.blocks.append(contact)
        elif kind == "or":
            if self.blocks:
                self.blocks[-1] = parallel(self.blocks[-1], contact)
            else:
                self.warn(ins, "OR with nothing to connect to - treated as LD")
                self.blocks.append(contact)
        elif op in ("ANB", "ORB"):
            if len(self.blocks) >= 2:
                b = self.blocks.pop()
                a = self.blocks.pop()
                self.blocks.append(series(a, b) if op == "ANB" else parallel(a, b))
            elif op == "ORB" or not self.has_output:
                self.warn(ins, "not enough logic blocks on the stack")
            # ANB with one block right after an output: block joins the branch point
        elif op in ("INV", "MEP", "MEF"):
            c = Contact("", op)
            if self.blocks:
                self.blocks[-1] = series(self.blocks[-1], c)
            else:
                self.blocks.append(c)
        elif op == "MPS":
            self._attach_pending(ins)
            self.mps.append(self.anchor)
        elif op in ("MRD", "MPP"):
            if self._pending(ins) is not None:
                self.warn(ins, "logic before MRD/MPP has no output - dropped")
            if not self.mps:
                self.warn(ins, "MRD/MPP without MPS")
            else:
                self.anchor = self.mps[-1] if op == "MRD" else self.mps.pop()
        else:  # output / applied instruction
            self._attach_pending(ins)
            self.anchor.children.append(Output(op, list(ins.args), ins.step))
            self.has_output = True

    def finish(self) -> Rung:
        if self.blocks:
            cond = self._pending(self.rung.source[-1])
            self.anchor.children.append(Branch(cond, [Output("???", [])]))
            self.rung.warnings.append("rung ends without an output")
        if self.mps:
            self.rung.warnings.append(f"{len(self.mps)} MPS without MPP")
        return self.rung


def _ld_continues_rung(instrs: List[Instr], i: int) -> bool:
    """After an output, an LD normally starts a new rung - unless an ANB later joins the
    new block back onto the current branch point (GX writes 'OUT Y0 / LD X1 / OR X2 / ANB')."""
    depth = 0
    for ins in instrs[i:]:
        kind, _ = classify(ins)
        if kind == "start":
            depth += 1
        elif ins.op == "ORB":
            depth -= 1
        elif ins.op == "ANB":
            depth -= 1
            if depth == 0:
                return True
        elif kind is None and ins.op not in ("INV", "MEP", "MEF"):
            return False  # reached an output / stack op first
    return False


def build(instrs: List[Instr]) -> List[Rung]:
    rungs: List[Rung] = []
    label = ""
    rb: Optional[_RungBuilder] = None

    def close():
        nonlocal rb
        if rb is not None and not rb.empty:
            rungs.append(rb.finish())
        rb = None

    for i, ins in enumerate(instrs):
        if ins.op in IGNORED:
            continue
        if POINTER_RE.match(ins.op) and not ins.args:
            close()
            label = ins.op
            continue
        kind, _ = classify(ins)
        if rb is not None and rb.has_output and kind == "start" and not rb.mps \
                and not _ld_continues_rung(instrs, i):
            close()
        if ins.op in UNCONDITIONAL and (rb is None or rb.empty or rb.has_output) \
                and (rb is None or not rb.blocks):
            close()
        if rb is None:
            rb = _RungBuilder(len(rungs), label)
            label = ""
        rb.feed(ins)
        if ins.op in UNCONDITIONAL and len(rb.rung.source) == 1:
            close()
    close()
    for n, r in enumerate(rungs):
        r.index = n
    return rungs
