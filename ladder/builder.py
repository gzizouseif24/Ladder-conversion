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

from . import dialects
from .model import Branch, Contact, Instr, Output, Rung, parallel, series

_PULSE = {"": "NO", "I": "NC", "P": "P", "F": "F", "PI": "PI", "FI": "FI"}
CONTACT_OPS = {}
for _base, _kind in (("LD", "start"), ("AND", "and"), ("OR", "or")):
    for _suffix, _ckind in _PULSE.items():
        _op = _base + _suffix
        if _base == "AND" and _suffix == "I":
            _op = "ANI"
        CONTACT_OPS[_op] = (_kind, _ckind)

_CMP_RE = re.compile(r"^(LD|AND|OR|ST|AN)(D|E|DE|F)?(=|<>|>=|<=|>|<)$")

# Instructions that need no input condition and stand on their own rung.
UNCONDITIONAL = {"END", "FEND", "RET", "SRET", "IRET", "NEXT", "MCR", "EI", "DI", "BREAK",
                 "ED", "CNDE", "MCE", "STPE"}
IGNORED = {"NOP", "NOPLF", "PAGE"}
POINTER_RE = re.compile(r"^[PI]\d+$")


def classify(ins: Instr):
    """Return (kind, contact) where kind is start/and/or or None."""
    op = dialects.canonical(ins.op)
    if op in CONTACT_OPS:
        kind, ckind = CONTACT_OPS[op]
        return kind, Contact(ins.args[0] if ins.args else "?", ckind)
    m = _CMP_RE.match(ins.op)
    if m:
        kind = {"LD": "start", "ST": "start", "AND": "and", "AN": "and", "OR": "or"}[m.group(1)]
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
        # True while blocks[0] continues from self.anchor (AND/DF right after an output) rather
        # than from the left rail.
        self.anchored = False
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

    def _path_expr(self, target: Branch):
        """Series of all branch expressions from the left rail down to `target`."""
        def walk(br, acc):
            acc = series(acc, br.expr)
            if br is target:
                return acc, True
            for c in br.children:
                if isinstance(c, Branch):
                    found, ok = walk(c, acc)
                    if ok:
                        return found, True
            return None, False
        return walk(self.root, None)[0]

    def _materialize(self):
        """blocks[0] continues from the anchor; make it start at the left rail instead, so it
        can be ORed with blocks that do (redraws the path to the anchor)."""
        if self.anchored and self.blocks:
            self.blocks[0] = series(self._path_expr(self.anchor), self.blocks[0])
            self.anchor = self.root
        self.anchored = False

    def _pending(self, ins: Instr):
        if len(self.blocks) > 1:
            self.warn(ins, f"{len(self.blocks)} unjoined logic blocks (missing ANB/ORB?) - joined in series")
        cond = None
        for b in self.blocks:
            cond = series(cond, b)
        self.blocks = []
        self.anchored = False
        return cond

    def _attach_pending(self, ins: Instr):
        cond = self._pending(ins)
        if cond is not None:
            node = Branch(cond)
            self.anchor.children.append(node)
            self.anchor = node

    def feed(self, ins: Instr, detached: bool = False):
        self.add_source(ins)
        op = dialects.canonical(ins.op)
        kind, contact = classify(ins)
        if kind == "start":
            if detached:
                # new logic from the left rail inside a PSHS/POPS (MPS/MPP) section
                self.anchor = self.root
            self.blocks.append(contact)
        elif kind == "and":
            if self.blocks:
                self.blocks[-1] = series(self.blocks[-1], contact)
            else:
                self.anchored = self.anchor is not self.root
                self.blocks.append(contact)
        elif kind == "or":
            if self.blocks:
                self.blocks[-1] = parallel(self.blocks[-1], contact)
            elif self.anchor is not self.root:
                # OR right after an output (allowed on Panasonic/Mitsubishi): the result so far is
                # the whole path to the branch point. Redraw that path in parallel, from the rail.
                path = self._path_expr(self.anchor)
                self.anchor = self.root
                self.blocks.append(parallel(path, contact))
            else:
                self.warn(ins, "OR with nothing to connect to - treated as LD")
                self.blocks.append(contact)
        elif op in ("ANB", "ORB"):
            if op == "ORB" and len(self.blocks) == 2:
                self._materialize()
            if len(self.blocks) >= 2:
                b = self.blocks.pop()
                a = self.blocks.pop()
                self.blocks.append(series(a, b) if op == "ANB" else parallel(a, b))
            elif op == "ORB" or not self.has_output:
                self.warn(ins, "not enough logic blocks on the stack")
            # ANB with one block right after an output: block joins the branch point
        elif op in ("INV", "MEP", "MEF"):
            c = Contact("", op, ins.op if ins.op != op else "")
            if self.blocks:
                self.blocks[-1] = series(self.blocks[-1], c)
            else:
                self.anchored = self.anchor is not self.root
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
            out = Output(ins.op, list(ins.args), ins.step)
            names = dialects.MULTI_INPUT.get(dialects.multi_input_key(ins.op))
            if len(self.blocks) > 1 or (names and len(self.blocks) == len(names)):
                self._materialize()
                if not names:
                    self.warn(ins, f"{len(self.blocks)} logic blocks feed this instruction - drawn as separate inputs")
                elif len(names) != len(self.blocks):
                    self.warn(ins, f"expected {len(names)} inputs, found {len(self.blocks)}")
                out.inputs = self.blocks
                out.input_names = (names or [])[:len(self.blocks)]
                self.blocks = []
            else:
                self._attach_pending(ins)
            self.anchor.children.append(out)
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
        op = dialects.canonical(ins.op)
        if kind == "start":
            depth += 1
        elif op == "ORB":
            depth -= 1
        elif op == "ANB":
            depth -= 1
            if depth == 0:
                return True
        elif op in IGNORED:
            continue
        elif kind is None and op not in ("INV", "MEP", "MEF"):
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
        if (POINTER_RE.match(ins.op) and not ins.args) or ins.op in dialects.LABEL_OPS:
            close()
            label = str(ins)
            continue
        kind, _ = classify(ins)
        detached = False
        if rb is not None and kind == "start" and not rb.blocks and (rb.mps or rb.has_output) \
                and not _ld_continues_rung(instrs, i):
            if rb.mps:
                detached = True
            else:
                close()
        if ins.op in UNCONDITIONAL and (rb is None or rb.empty or rb.has_output) \
                and (rb is None or not rb.blocks):
            close()
        if rb is None:
            rb = _RungBuilder(len(rungs), label)
            label = ""
        rb.feed(ins, detached)
        if ins.op in UNCONDITIONAL and len(rb.rung.source) == 1:
            close()
    close()
    for n, r in enumerate(rungs):
        r.index = n
    return rungs
