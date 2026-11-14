"""Siemens STEP 7 (S7-300/400) STL / AWL source -> ladder rungs.

Input: the text file from STEP 7 "Generate Source" (.AWL), German (U, UN, SPBNB, ZV ...) or English
(A, AN, JNB, CU ...) mnemonics.

Unlike the stack-based dialects, STL works on an RLO (result of logic operation) built left to right,
with parentheses (A( ... )), "O" alone (AND before OR), first-check after every assignment, an
accumulator for word logic, and jumps. This module evaluates a network into statements:

    condition (contact tree)  ->  output (coil, set/reset, timer, counter, MOVE/math box, call ...)

and recognises the code STEP 7 generates for LAD/FBD boxes, so they come back as boxes:

    L S5T#3S / SE T20 / NOP 0 x3 / U T20 / = M150.1      timer box with Q driving a coil
    SPBNB _001 / L .. / T .. / _001: U BIE                 MOVE / ADD / MUL ... with EN and ENO
    ZV Z30 / ... / R Z30 / LC Z30 / T MW230                counter box with its pins

Anything it does not understand makes the whole network an "STL only" rung with a warning, the same
thing STEP 7 does when it cannot show a network in LAD.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import Branch, Contact, Output, Rung, parallel, series

# ---------------------------------------------------------------- mnemonics

GERMAN_TO_ENGLISH = {
    "U": "A", "UN": "AN", "U(": "A(", "UN(": "AN(",
    "SPA": "JU", "SPB": "JC", "SPBN": "JCN", "SPBB": "JCB", "SPBNB": "JNB", "SPBI": "JBI",
    "SPBIN": "JNBI", "SPO": "JO", "SPS": "JOS", "SPZ": "JZ", "SPN": "JN", "SPP": "JP", "SPM": "JM",
    "SPMZ": "JMZ", "SPPZ": "JPZ", "SPU": "JUO", "SPL": "JL",
    "BEB": "BEC", "BEA": "BEU", "ZV": "CU", "ZR": "CD",
    "SI": "SP", "SV": "SE", "SE": "SD", "SS": "SS", "SA": "SF",   # timers: German SE = on-delay
}
TIMER_OPS = {"SP", "SE", "SD", "SS", "SF"}
TIMER_NAMES = {  # canonical op -> (English LAD box, German LAD box)
    "SP": ("S_PULSE", "S_IMPULS"), "SE": ("S_PEXT", "S_VIMP"), "SD": ("S_ODT", "S_EVERZ"),
    "SS": ("S_ODTS", "S_SEVERZ"), "SF": ("S_OFFDT", "S_AVERZ"),
}
ARITH = {
    "+I": "ADD_I", "-I": "SUB_I", "*I": "MUL_I", "/I": "DIV_I",
    "+D": "ADD_DI", "-D": "SUB_DI", "*D": "MUL_DI", "/D": "DIV_DI", "MOD": "MOD_DI",
    "+R": "ADD_R", "-R": "SUB_R", "*R": "MUL_R", "/R": "DIV_R",
}
COMPARE_RE = re.compile(r"^(==|<>|>=|<=|>|<)([IDR])$")
COND_JUMPS = {"JC", "JCN", "JCB", "JNB", "JBI", "JNBI"}
SKIP_OPS = {"NOP", "BLD"}
BR_OPERANDS = {"BIE", "BR"}
OV_OPERANDS = {"OV", "OS"}


class Unsupported(Exception):
    pass


# ---------------------------------------------------------------- parsing

@dataclass
class Stmt:
    op: str            # canonical (English) mnemonic
    arg: str           # operand with spaces removed: "M201.0", "T20", "DB122.DBD8"
    label: str = ""
    line: int = 0
    raw: str = ""      # as written (German or English)

    def __str__(self):
        s = f"{self.raw:<6} {self.arg}".rstrip()
        return f"{self.label}: {s}" if self.label else s


@dataclass
class Network:
    index: int
    title: str = ""
    stmts: List[Stmt] = field(default_factory=list)


@dataclass
class Block:
    name: str          # "FC1", "OB1", "FB10"
    title: str = ""    # NAME : MANUELL
    networks: List[Network] = field(default_factory=list)


_HEADER_RE = re.compile(r"^(FUNCTION_BLOCK|FUNCTION|ORGANIZATION_BLOCK|DATA_BLOCK|TYPE)\s+(.+?)(\s*:\s*\w+)?\s*$")
_LABEL_RE = re.compile(r"^([A-Za-z_]\w{0,3})\s*:\s*(.*)$")


def is_siemens(text: str) -> bool:
    return bool(re.search(r"^\s*(FUNCTION|FUNCTION_BLOCK|ORGANIZATION_BLOCK)\s", text, re.M)) \
        and bool(re.search(r"^\s*NETWORK\s*$", text, re.M))


def detect_language(text: str) -> str:
    de = len(re.findall(r"^\s*(?:\w+:\s*)?(U|UN|U\(|UN\(|SPB\w*|SPA|ZV|ZR)\s", text, re.M))
    en = len(re.findall(r"^\s*(?:\w+:\s*)?(A|AN|A\(|AN\(|JC\w*|JU|JNB|CU|CD)\s", text, re.M))
    return "de" if de >= en else "en"


def _operand(rest: str) -> str:
    rest = rest.strip()
    if '"' in rest or "#" in rest and rest.startswith("#"):
        return rest
    return re.sub(r"\s+", "", rest)


def parse_awl(text: str):
    """Return (blocks, language)."""
    lang = detect_language(text)
    blocks: List[Block] = []
    blk: Optional[Block] = None
    net: Optional[Network] = None
    mode = "out"  # out | header | var | code | skip
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        code, _, comment = line.partition("//")
        s = code.strip()
        m = _HEADER_RE.match(s)
        if m and mode in ("out", "header"):
            kind = m.group(1)
            if kind in ("DATA_BLOCK", "TYPE"):
                mode = "skip"
                continue
            blk = Block(re.sub(r"\s+", "", m.group(2)).strip())
            blocks.append(blk)
            net = None
            mode = "header"
            continue
        if mode == "skip":
            if s.startswith(("END_DATA_BLOCK", "END_TYPE")):
                mode = "out"
            continue
        if s.startswith("END_") and not s.startswith("END_VAR") and not s.startswith("END_STRUCT"):
            mode = "out"
            continue
        if mode == "header":
            if s.startswith("VAR"):
                mode = "var"
            elif s == "BEGIN":
                mode = "code"
            elif s.startswith("NAME") and ":" in s and blk is not None:
                blk.title = s.split(":", 1)[1].strip()
            continue
        if mode == "var":
            if s.startswith("END_VAR"):
                mode = "header"
            continue
        if mode != "code" or blk is None:
            continue
        if s == "NETWORK":
            net = Network(len(blk.networks) + 1)
            blk.networks.append(net)
            continue
        if net is None:
            continue
        if s.startswith("TITLE") and "=" in s and not net.stmts:
            net.title = s.split("=", 1)[1].strip()
            continue
        if not s:
            continue
        label = ""
        lm = _LABEL_RE.match(s)
        if lm and not s.startswith(("TITLE",)):
            label, s = lm.group(1), lm.group(2)
        s = s.rstrip(";").strip()
        if not s:
            if label:
                net.stmts.append(Stmt("NOP", "0", label, lineno, "NOP"))
            continue
        parts = s.split(None, 1)
        rawop = parts[0]
        op = GERMAN_TO_ENGLISH.get(rawop, rawop) if lang == "de" else rawop
        arg = _operand(parts[1]) if len(parts) > 1 else ""
        net.stmts.append(Stmt(op, arg, label, lineno, rawop))
    return blocks, lang


# ---------------------------------------------------------------- evaluation

_TRUE = None  # "no condition" = power straight from the rail


@dataclass
class _Level:
    op: str = ""                       # how this parenthesis level joins its parent: A( AN( O( ON(
    terms: list = field(default_factory=list)   # OR terms closed by "O" alone
    cur: object = None
    fc: bool = True                    # first check: next logic op starts a new chain
    cached: object = None              # RLO kept after an assignment (= x / = y share it)

    def value(self):
        v = None
        for t in self.terms + [self.cur]:
            v = parallel(v, t)
        return v


@dataclass
class _Entry:
    cond: object
    out: Output
    kind: str          # bit | acc | timer | counter | jump | call | end
    depth: int
    key: str = ""      # timer / counter address


@dataclass
class _Box:
    out: Output
    inner: object      # what feeds the box (its input / EN)
    ref: Optional[Contact]  # what replaces the box if it has to be moved to its own rung


def _negate(e):
    if isinstance(e, Contact) and e.kind in ("NO", "NC") and not e.box:
        return Contact(e.device, "NC" if e.kind == "NO" else "NO")
    return series(e, Contact("", "INV", "NOT"))


def _contains_box(e) -> bool:
    if isinstance(e, Contact):
        return e.box is not None
    items = getattr(e, "items", None) or getattr(e, "branches", None) or []
    return any(_contains_box(i) for i in items)


def _dedupe(e):
    """A·A = A: drop a plain contact repeated in the same series (EN condition + same contact)."""
    if not hasattr(e, "items"):
        return e
    seen, items = [], []
    for i in e.items:
        if isinstance(i, Contact) and i.kind in ("NO", "NC") and i.box is None:
            if any(i == j for j in seen):
                continue
            seen.append(i)
        items.append(i)
    return items[0] if len(items) == 1 else type(e)(items)


def _tv_text(tv: str) -> str:
    """W#16#1100 -> 'W#16#1100 (10s)' (time base + BCD value)."""
    m = re.match(r"^W#16#([0-3])(\d{3})$", tv)
    if not m:
        return tv
    secs = int(m.group(2)) * (0.01, 0.1, 1, 10)[int(m.group(1))]
    return f"{tv} ({secs:g}s)"


class NetworkBuilder:
    def __init__(self, net: Network, lang: str, labels_elsewhere: set):
        self.net = net
        self.lang = lang
        self.de = lang == "de"
        self.labels_elsewhere = labels_elsewhere
        self.levels: List[_Level] = [_Level()]
        self.entries: List[_Entry] = []
        self.enables: List[dict] = []      # open EN sections (forward conditional jumps)
        self.acc1 = None                   # ("val"|"bcd"|"op", text, ...)
        self.acc2 = None
        self.timers: Dict[str, _Entry] = {}
        self.counters: Dict[str, _Entry] = {}
        self.warnings: List[str] = []
        self.ended = False
        self._wrap_memo: list = []

    # ---- helpers
    @property
    def lv(self) -> _Level:
        return self.levels[-1]

    def rlo(self):
        lv = self.lv
        if lv.fc:
            return lv.cached
        return lv.value()

    def _wrap(self, cond):
        """Prefix the EN conditions of open jump sections. Memoised, so outputs that share one RLO
        keep sharing one condition object (and end up on one rung)."""
        if not self.enables:
            return cond
        ens = tuple(en["enable"] for en in self.enables)
        for c, e, out in self._wrap_memo:
            if c is cond and len(e) == len(ens) and all(a is b for a, b in zip(e, ens)):
                return out
        out = cond
        for en in reversed(ens):
            out = series(en, out)
        out = _dedupe(out)
        self._wrap_memo.append((cond, ens, out))  # keeps the objects alive: identities stay valid
        return out

    def emit(self, cond, out: Output, kind: str, key: str = "") -> _Entry:
        e = _Entry(self._wrap(cond), out, kind, len(self.levels), key)
        self.entries.append(e)
        return e

    def after_output(self, cond):
        self.lv.cached = cond
        self.lv.fc = True

    def logic(self, how: str, e):
        lv = self.lv
        if lv.fc:
            lv.terms, lv.cur, lv.cached, lv.fc = [], e, None, False
        elif how == "A":
            if lv.cur is not None and _contains_box(e):
                e = self._hoist(e)
            lv.cur = series(lv.cur, e) if lv.cur is not None else e
        else:
            lv.cur = parallel(lv.cur, e)

    def _hoist(self, e):
        """A box would be fed by contacts to its left that are not its input: give it its own rung."""
        if isinstance(e, Contact):
            if e.box is None:
                return e
            self.entries.append(_Entry(e.box.inner, e.box.out, "box", len(self.levels)))
            return e.box.ref
        if hasattr(e, "items"):
            out = None
            for i in e.items:
                if isinstance(i, Contact) and i.box is not None:
                    self.entries.append(_Entry(i.box.inner, i.box.out, "box", len(self.levels)))
                    if i.box.ref is not None:
                        out = i.box.ref    # contacts to its left were the box input: now in its rung
                    # EN/ENO box (no ref): ENO = EN, the contacts to its left stay
                else:
                    out = series(out, self._hoist(i))
            return out
        if hasattr(e, "branches"):
            out = None
            for b in e.branches:
                out = parallel(out, self._hoist(b))
            return out
        return e

    def box_contact(self, out: Output, inner, ref) -> Contact:
        return Contact("", "BOX", out.text, box=_Box(out, inner, ref))

    def contact(self, op: str, arg: str) -> Contact:
        return Contact(arg, "NC" if op.endswith("N") else "NO")

    # ---- main loop
    def run(self) -> List[_Entry]:
        st = self.net.stmts
        i = 0
        while i < len(st):
            s = st[i]
            if self.ended:
                break
            if s.label and self.enables and self.enables[-1]["label"] == s.label:
                en = self.enables.pop()
                self.levels[en["depth"] - 1].fc = True
                self.levels[en["depth"] - 1].cached = None
            i = self.step(st, i)
        if len(self.levels) > 1:
            raise Unsupported("unbalanced parentheses")
        return self.entries

    def step(self, st: List[Stmt], i: int) -> int:
        s = st[i]
        op, arg = s.op, s.arg
        nxt = st[i + 1] if i + 1 < len(st) else None

        if op in SKIP_OPS:
            return i + 1
        # ENO plumbing STEP 7 adds after boxes: "UN OV / SAVE / CLR" and "SET / SAVE / CLR"
        if op in ("A", "AN") and arg in OV_OPERANDS and nxt is not None and nxt.op == "SAVE":
            return self._skip_save(st, i + 2)
        if op == "SET" and nxt is not None and nxt.op == "SAVE":
            return self._skip_save(st, i + 2)

        if op in ("A", "AN", "O", "ON") and arg:
            if arg in BR_OPERANDS and op == "A":
                self.eno()
                return i + 1
            if op == "A" and self.lv.fc and self._timer_inline(arg):
                return i + 1
            self.logic("A" if op in ("A", "AN") else "O", self.contact(op, arg))
            return i + 1
        if op == "O" and not arg:            # AND before OR
            lv = self.lv
            if lv.fc:
                lv.terms, lv.cur, lv.cached, lv.fc = [], None, None, False
            lv.terms.append(lv.cur)
            lv.cur = None
            return i + 1
        if op in ("A(", "AN(", "O(", "ON("):
            if self.lv.fc:
                self.lv.terms, self.lv.cur, self.lv.cached, self.lv.fc = [], None, None, False
            self.levels.append(_Level(op))
            return i + 1
        if op == ")":
            if len(self.levels) == 1:
                raise Unsupported(") without (")
            lv = self.levels.pop()
            val = lv.value() if not lv.fc else (lv.cached if lv.cached is not None else lv.value())
            if lv.op in ("AN(", "ON("):
                val = _negate(val)
            parent = self.lv
            if parent.cur is None and not parent.terms:
                parent.cur = val
            elif lv.op in ("A(", "AN("):
                if parent.cur is not None and _contains_box(val):
                    val = self._hoist(val)
                parent.cur = series(parent.cur, val) if parent.cur is not None else val
            else:
                parent.cur = parallel(parent.cur, val)
            parent.fc = False
            return i + 1
        if op in ("FP", "FN"):
            self.logic("A", Contact(arg, "P" if op == "FP" else "F"))
            return i + 1
        if op == "NOT":
            lv = self.lv
            val = lv.value()
            lv.terms, lv.cur = [], _negate(val)
            return i + 1
        if COMPARE_RE.match(op):
            a = self._acc_text(self.acc2)
            b = self._acc_text(self.acc1)
            self.logic("A", Contact("", "CMP", f"{op} {a} {b}"))
            return i + 1

        # ---- assignments
        if op in ("=", "S", "R"):
            self.assign(op, arg)
            return i + 1
        if op in TIMER_OPS:
            self.timer(op, arg)
            return i + 1
        if op in ("CU", "CD"):
            self.counter(op, arg)
            return i + 1

        # ---- accumulator
        if op in ("L", "LC"):
            self.acc2, self.acc1 = self.acc1, ("bcd" if op == "LC" else "val", arg)
            return i + 1
        if op in ARITH:
            self.acc1 = ("op", ARITH[op], self._acc_text(self.acc2), self._acc_text(self.acc1))
            return i + 1
        if op == "T":
            self.transfer(arg)
            return i + 1

        # ---- jumps, calls, block end
        if op in COND_JUMPS or op == "JU":
            return self.jump(st, i)
        if op in ("UC", "CALL"):
            self.emit(None, Output("CALL", [arg]), "call")
            return i + 1
        if op == "CC":
            c = self.rlo()
            self.emit(c, Output("CALL", [arg]), "call")
            self.after_output(c)
            return i + 1
        if op in ("BE", "BEU"):
            self.emit(None, Output(op if op == "BE" else "BEA" if self.de else "BEU", []), "end")
            self.ended = True
            return i + 1
        if op == "BEC":
            c = self.rlo()
            self.emit(c, Output("BEB" if self.de else "BEC", []), "end")
            self.after_output(c)
            return i + 1
        if op in ("SAVE", "CLR", "SET"):
            raise Unsupported(s.raw)
        raise Unsupported(s.raw)

    def _skip_save(self, st, i):
        while i < len(st) and st[i].op in ("CLR",) and not st[i].label:
            i += 1
        return i

    # ---- outputs
    def assign(self, op: str, arg: str):
        c = self.rlo()
        is_t = re.match(r"^T\d+$", arg)
        is_c = re.match(r"^[ZC]\d+$", arg)
        if op == "R" and is_t and arg in self.timers:
            e = self.timers[arg]
            if not e.out.inputs:
                e.out.inputs = [e.cond]
                e.out.input_names = ["S"]
                e.cond = None
            e.out.inputs.append(self._wrap(c))
            e.out.input_names.append("R")
        elif is_c and op in ("S", "R"):
            self.counter(op, arg)
            return
        else:
            self.emit(c, Output(op, [arg]), "bit")
        self.after_output(c)

    def timer(self, op: str, arg: str):
        c = self.rlo()
        name = TIMER_NAMES[op][1 if self.de else 0]
        tv = _tv_text(self._acc_text(self.acc1))
        e = self.emit(c, Output(name, [arg, tv]), "timer", arg)
        self.timers[arg] = e
        self.after_output(c)

    def counter(self, op: str, arg: str):
        craw = self.rlo()
        c = self._wrap(craw)
        pin = {"CU": "ZV" if self.de else "CU", "CD": "ZR" if self.de else "CD"}.get(op, op)
        e = self.counters.get(arg)
        if e is None:
            e = _Entry(None, Output("", [arg], inputs=[], input_names=[]), "counter", len(self.levels), arg)
            self.entries.append(e)
            self.counters[arg] = e
        e.out.inputs.append(c)
        e.out.input_names.append(pin)
        if op == "S":
            e.out.args.append(("ZW=" if self.de else "PV=") + self._acc_text(self.acc1))
        pins = set(e.out.input_names)
        up = ("ZV" if self.de else "CU") in pins
        down = ("ZR" if self.de else "CD") in pins
        if self.de:
            e.out.op = "ZAEHLER" if up and down else ("Z_RUECK" if down else "Z_VORW")
        else:
            e.out.op = "S_CUD" if up and down else ("S_CD" if down else "S_CU")
        self.after_output(craw)

    def _timer_inline(self, arg: str) -> bool:
        """'... SE T20 / NOP 0 x3 / U T20 / = ...': draw the timer box in the rung, Q to the right."""
        e = self.timers.get(arg)
        if e is None or not self.entries or self.entries[-1] is not e or e.out.inputs \
                or e.depth != len(self.levels) or self.enables:
            return False
        self.entries.pop()
        self.timers.pop(arg)
        box = self.box_contact(e.out, e.cond, Contact(arg))
        lv = self.lv
        lv.terms, lv.cur, lv.cached, lv.fc = [], series(e.cond, box), None, False
        return True

    # ---- accumulator
    def _acc_text(self, a) -> str:
        if a is None:
            return "?"
        if a[0] == "op":
            return f"{a[1]}({a[2]}, {a[3]})"
        return a[1]

    def transfer(self, dest: str):
        a = self.acc1
        if a is None:
            raise Unsupported("T without L")
        src = a[1]
        # L T20 / LC T20 / L Z30 / LC Z30 then T x: an output pin of the timer/counter box
        if a[0] in ("val", "bcd") and (src in self.timers or src in self.counters):
            e = self.timers.get(src) or self.counters.get(src)
            if src in self.timers:
                pin = ("DEZ" if self.de else "BCD") if a[0] == "bcd" else ("DUAL" if self.de else "BI")
            else:
                pin = ("DEZ" if self.de else "CV_BCD") if a[0] == "bcd" else ("DUAL" if self.de else "CV")
            e.out.args.append(f"{pin}→{dest}")
            return
        last = self.entries[-1] if self.entries else None
        if a[0] == "op":
            _, name, x, y = a
            if last is not None and last.kind == "acc" and last.out.op == name and last.out.args[:2] == [x, y] \
                    and last.depth == len(self.levels):
                last.out.args.append(dest)
                return
            self.emit(None, Output(name, [x, y, "→", dest]), "acc")
        else:
            if last is not None and last.kind == "acc" and last.out.op == "MOVE" and last.out.args[0] == src \
                    and last.depth == len(self.levels):
                last.out.args.append(dest)
                return
            self.emit(None, Output("MOVE", [src, "→", dest]), "acc")

    def eno(self):
        """U BIE after a box: continue the rung from the box's ENO (box drawn in series)."""
        boxes = []
        k = len(self.entries)
        while k > 0 and self.entries[k - 1].kind == "acc" and self.entries[k - 1].depth == len(self.levels):
            k -= 1
        trailing = self.entries[k:]
        if trailing and all(t.cond is trailing[0].cond for t in trailing):
            del self.entries[k:]
            en = trailing[0].cond
            for t in trailing:
                boxes.append(self.box_contact(t.out, en, None))
            expr = en
            for b in boxes:
                expr = series(expr, b)
            lv = self.lv
            lv.terms, lv.cur, lv.cached, lv.fc = [], expr, None, False
        else:
            self.logic("A", Contact("BIE" if self.de else "BR"))

    # ---- jumps
    def jump(self, st: List[Stmt], i: int) -> int:
        s = st[i]
        target = next((k for k in range(i + 1, len(st)) if st[k].label == s.arg), None)
        c = self.rlo()
        if target is None:
            # jump to another network: draw a jump coil, the target network gets the label
            name = {"JU": "SPA", "JC": "SPB", "JCN": "SPBN", "JNB": "SPBNB", "JCB": "SPBB"}.get(s.op, s.raw) \
                if self.de else s.op
            cond = None if s.op == "JU" else (c if s.op in ("JC", "JCB") else _negate(c))
            self.emit(cond, Output("JMP" if s.op != "JU" else "JMP", [s.arg, f"({name})"]), "jump")
            self.after_output(c)
            return i + 1
        if s.op == "JU":
            self.warnings.append(f"code after SPA/JU {s.arg} skipped (never runs)")
            return target
        # forward conditional jump inside the network: the skipped code runs only when ...
        enable = c if s.op in ("JNB", "JCN", "JNBI") else _negate(c)
        self.enables.append({"label": s.arg, "enable": enable, "depth": len(self.levels)})
        self.lv.fc, self.lv.cached = True, None
        return i + 1


def _rungs_from_entries(entries: List[_Entry]) -> List[Branch]:
    """Consecutive outputs sharing one RLO become one rung with parallel outputs."""
    roots: List[Branch] = []
    prev = None
    for e in entries:
        joinable = not e.out.inputs
        if joinable and prev is not None and e.cond is prev.cond and not prev.out.inputs:
            target = roots[-1].children[-1] if e.cond is not None else roots[-1]
            target.children.append(e.out)
        else:
            roots.append(Branch(None, [Branch(e.cond, [e.out])] if e.cond is not None and joinable
                                else [e.out]))
        prev = e
    return roots


def convert_text(text: str) -> List[Rung]:
    blocks, lang = parse_awl(text)
    rungs: List[Rung] = []
    for blk in blocks:
        labels = {s.label for n in blk.networks for s in n.stmts if s.label}
        for net in blk.networks:
            if not net.stmts:
                continue
            title = f"{blk.name}" + (f" · {blk.title}" if blk.title else "")
            head = f"Network {net.index}" + (f": {net.title}" if net.title else "")
            first_label = net.stmts[0].label
            nb = NetworkBuilder(net, lang, labels)
            try:
                roots = _rungs_from_entries(nb.run())
                warns = list(nb.warnings)
            except Unsupported as ex:
                roots = [Branch(None, [Output("STL", [f"{len(net.stmts)} instructions"])])]
                warns = [f"'{ex}' cannot be drawn as ladder - network shown as STL only (see instruction list)"]
            except Exception as ex:  # a converter bug must not lose the network
                roots = [Branch(None, [Output("STL", [f"{len(net.stmts)} instructions"])])]
                warns = [f"converter error ({type(ex).__name__}: {ex}) - network shown as STL only"]
            for k, root in enumerate(roots):
                r = Rung(root, None, len(rungs), first_label + ":" if (k == 0 and first_label) else "")
                r.block, r.block_title, r.network = blk.name, title, head
                r.source = list(net.stmts) if k == 0 else []
                r.warnings = warns if k == 0 else []
                rungs.append(r)
            if nb.ended and net is not blk.networks[-1]:
                rest = blk.networks[blk.networks.index(net) + 1:]
                if any(st.op not in ("BE", "NOP", "BLD") for n in rest for st in n.stmts):
                    rungs[-1].warnings = rungs[-1].warnings + [
                        f"{len(rest)} more network(s) after BE never run - not drawn"]
                break
    return rungs


def list_text(text: str) -> str:
    blocks, lang = parse_awl(text)
    out = [f"-- Siemens STL ({'German' if lang == 'de' else 'English'} mnemonics)"]
    for blk in blocks:
        out.append(f"\n{blk.name}  {blk.title}")
        for net in blk.networks:
            out.append(f"  Network {net.index}" + (f": {net.title}" if net.title else ""))
            out += [f"      {s}" for s in net.stmts]
    return "\n".join(out)
