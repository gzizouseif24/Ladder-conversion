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

from .model import Branch, Contact, Output, Rung, Series, parallel, series

# ---------------------------------------------------------------- mnemonics

GERMAN_TO_ENGLISH = {
    "U": "A", "UN": "AN", "U(": "A(", "UN(": "AN(",
    "SPA": "JU", "SPB": "JC", "SPBN": "JCN", "SPBB": "JCB", "SPBNB": "JNB", "SPBI": "JBI",
    "SPBIN": "JNBI", "SPO": "JO", "SPS": "JOS", "SPZ": "JZ", "SPN": "JN", "SPP": "JP", "SPM": "JM",
    "SPMZ": "JMZ", "SPPZ": "JPZ", "SPU": "JUO", "SPL": "JL",
    "BEB": "BEC", "BEA": "BEU", "ZV": "CU", "ZR": "CD", "UW": "AW", "UD": "AD", "AUF": "OPN",
    "SPL": "JL", "X(": "X(", "XN(": "XN(",
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
STATUS_JUMPS = {"JZ", "JN", "JP", "JM", "JMZ", "JPZ", "JO", "JOS", "JUO"}
ADDR_OPS = {"A", "AN", "O", "ON", "X", "XN", "=", "S", "R", "L", "T", "FP", "FN"}
WORD_LOGIC = {"AW": "WAND_W", "OW": "WOR_W", "XOW": "WXOR_W",
              "AD": "WAND_DW", "OD": "WOR_DW", "XOD": "WXOR_DW"}
SHIFTS = {"SLW": "SHL_W", "SRW": "SHR_W", "SLD": "SHL_DW", "SRD": "SHR_DW",
          "SSI": "SHR_I", "SSD": "SHR_DI", "RLD": "ROL_DW", "RRD": "ROR_DW"}
UNARY = {"ITD": "I_DI", "DTR": "DI_R", "ITB": "I_BCD", "BTI": "BCD_I", "DTB": "DI_BCD", "BTD": "BCD_DI",
         "RND": "ROUND", "TRUNC": "TRUNC", "RND+": "CEIL", "RND-": "FLOOR",
         "INVI": "INV_I", "INVD": "INV_DI", "NEGI": "NEG_I", "NEGD": "NEG_DI", "NEGR": "NEG_R",
         "ABS": "ABS", "SQR": "SQR", "SQRT": "SQRT", "EXP": "EXP", "LN": "LN",
         "SIN": "SIN", "COS": "COS", "TAN": "TAN", "ASIN": "ASIN", "ACOS": "ACOS", "ATAN": "ATAN",
         "TAW": "SWAP_W", "TAD": "SWAP_DW"}
UNSUPPORTED_WHY = {
    "JL": "jump list (SPL)", "TAK": "swaps the accumulators", "PUSH": "accumulator stack",
    "POP": "accumulator stack", "ENT": "accumulator stack", "LEAVE": "accumulator stack",
    "LAR1": "address register (indirect addressing)", "LAR2": "address register (indirect addressing)",
    "TAR1": "address register (indirect addressing)", "TAR2": "address register (indirect addressing)",
    "TAR": "address register (indirect addressing)", "CAR": "address register (indirect addressing)",
    "+AR1": "address register (indirect addressing)", "+AR2": "address register (indirect addressing)",
    "TDB": "swaps the open data blocks", "LOOP": "loop", "SAVE": "writes the BR bit directly",
    "BLD": "", "MCRA": "master control relay", "MCRD": "master control relay",
    "MCR(": "master control relay", ")MCR": "master control relay",
}
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
    params: List[str] = field(default_factory=list)   # CALL FB10,DB10 ( IN1 := ..., ... )

    def __str__(self):
        s = f"{self.raw:<6} {self.arg}".rstrip()
        if self.params:
            s += " (" + ", ".join(self.params) + ")"
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


def _finish_call(st: Stmt, lines: List[str]) -> Stmt:
    """'CALL FB 100 , DB 100 (' + 'IN0 := 0);' -> arg 'FB100,DB100', params ['IN0 := 0']."""
    text = " ".join(l.strip() for l in lines).rstrip(";").strip()
    text = re.sub(r"^CALL\s*", "", text, flags=re.I)
    head, _, rest = text.partition("(")
    st.arg = _operand(head)
    rest = rest.strip()
    if rest.endswith(")"):
        rest = rest[:-1]
    params, depth, cur = [], 0, ""
    for ch in rest:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            params.append(cur)
            cur = ""
        else:
            cur += ch
    params.append(cur)
    st.params = [re.sub(r"\s*:=\s*", " := ", re.sub(r"\s+", " ", p.strip())) for p in params if p.strip()]
    return st


def parse_awl(text: str):
    """Return (blocks, language)."""
    lang = detect_language(text)
    blocks: List[Block] = []
    blk: Optional[Block] = None
    net: Optional[Network] = None
    mode = "out"  # out | header | var | code | skip
    call = None   # (Stmt, [lines]) while reading a CALL parameter list
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
        if call is not None:                       # inside "CALL xx (" ... ");"
            call[1].append(s)
            if s.rstrip(";").rstrip().endswith(")"):
                net.stmts.append(_finish_call(*call))
                call = None
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
        if re.match(r"^CALL\b", s, re.I):
            if "(" in s and not s.endswith(")"):
                call = (Stmt("CALL", "", label, lineno, "CALL"), [s])
            else:
                net.stmts.append(_finish_call(Stmt("CALL", "", label, lineno, "CALL"), [s]))
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
    ens: tuple = ()    # EN conditions of the open jump sections (outer -> inner)
    raw: object = None # condition inside those sections


@dataclass
class _Box:
    out: Output
    inner: object      # what feeds the box (its input / EN)
    ref: Optional[Contact]  # what replaces the box if it has to be moved to its own rung


_CMP_NOT = {"==": "<>", "<>": "==", ">": "<=", "<=": ">", "<": ">=", ">=": "<"}


def _negate(e):
    if isinstance(e, Contact) and not e.box:
        if e.kind in ("NO", "NC"):
            return Contact(e.device, "NC" if e.kind == "NO" else "NO")
        if e.kind == "CMP":                     # NOT (a <>I b)  ->  a ==I b
            m = re.match(r"^(==|<>|>=|<=|>|<)(\S*)(.*)$", e.text)
            if m:
                return Contact("", "CMP", _CMP_NOT[m.group(1)] + m.group(2) + m.group(3))
    if hasattr(e, "items") and isinstance(e.items[-1], Contact) and e.items[-1].kind == "INV":
        rest = e.items[:-1]                     # NOT NOT x -> x
        return rest[0] if len(rest) == 1 else type(e)(rest)
    return series(e, Contact("", "INV", "NOT"))


def _xor(a, b):
    """a XOR b = a·/b + /a·b"""
    if a is None:
        return b
    return parallel(series(a, _negate(b)), series(_negate(a), b))


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
    def __init__(self, net: Network, lang: str, targets: set = frozenset(), external: set = frozenset()):
        self.net = net
        self.lang = lang
        self.de = lang == "de"
        self.targets = targets      # labels some jump in the block goes to
        self.external = external    # labels jumped to from another network or from further down
        self.rlo_dirty = False
        self.acc_version = 0
        self.open_db = ""
        self.open_di = ""
        self.ended_network = False
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
        if self.rlo_dirty:
            raise Unsupported("logic result depends on which jump was taken")
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
        e = _Entry(self._wrap(cond), out, kind, len(self.levels), key,
                   tuple(en["enable"] for en in self.enables), cond)
        self.entries.append(e)
        return e

    def after_output(self, cond):
        self.lv.cached = cond
        self.lv.fc = True

    def logic(self, how: str, e):
        lv = self.lv
        if lv.fc:
            self._check_fresh(how)
            lv.terms, lv.cur, lv.cached, lv.fc = [], e, None, False
        elif how == "X":
            lv.cur = _xor(lv.cur, e)
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
        return Contact(arg, "NC" if op in ("AN", "ON", "XN") else "NO")

    # ---- main loop
    def run(self) -> List[_Entry]:
        st = self.net.stmts
        i = 0
        while i < len(st):
            if self.ended:
                break
            s = st[i]
            if s.label:
                self.at_label(s.label, i)
            i = self.step(st, i)
        if len(self.levels) > 1:
            raise Unsupported("brackets ( ) not closed in this network")
        return self.entries

    # ---- labels and jumps
    def at_label(self, label: str, i: int):
        open_labels = [en["label"] for en in self.enables]
        if label in open_labels:
            # Close every section ending here. A section opened later (inside it) that ends further
            # down stays open, but its code now also runs when this jump was taken:
            #   SPB L1 ... SPB L2 ... L1: <runs if jumped to L1, or if no jump to L2> ... L2:
            k = len(self.enables) - 1
            while k >= 0:
                en = self.enables[k]
                if en["label"] == label:
                    del self.enables[k]
                    inner = self.enables[k:]
                    if inner and all(x["label"] == inner[0]["label"] for x in inner):
                        # all jump to the same place: (c + a)(c + b) = c + a·b, one section
                        both = None
                        for x in inner:
                            both = series(both, x["enable"])
                        inner[0]["enable"] = parallel(_negate(en["enable"]), both)
                        del self.enables[k + 1:]
                    else:
                        for x in inner:
                            x["enable"] = parallel(_negate(en["enable"]), x["enable"])
                    self.close_section(en)
                k -= 1
        elif label in self.external and i > 0:
            raise Unsupported(f"label {label} is jumped to from another network or from further down")

    def close_section(self, en: dict):
        if len(self.levels) != en["depth"]:
            raise Unsupported("jump into or out of brackets ( )")
        lv = self.lv
        if en["live"]:
            # Two ways to get here: by the jump (RLO restarts) and by running through.
            if not lv.fc:
                self.rlo_dirty = "chain"     # running through, the logic chain is still open
            elif lv.cached is not None:
                self.rlo_dirty = "value"     # an RLO is kept for further = / S / R
            if self.acc_version != en["acc_version"]:
                self.acc1 = self.acc2 = ("dirty",)
        lv.fc, lv.cached = True, None
        if not en["live"] and en.get("exit"):
            # only the jump leads here: the rest runs under "the jump was taken"
            self.enables.append({"label": None, "enable": _negate(en["enable"]), "depth": en["depth"],
                                 "acc_version": self.acc_version, "live": True})

    def skip_dead(self, st: List[Stmt], i: int, exit_path: bool = True) -> int:
        """After SPA / BEA: continue at the next label something can still jump to.
        exit_path: the code that ran through left (BEA, jump elsewhere), so after the label only
        the jump path is left and the code there needs the jump condition."""
        open_labels = {en["label"] for en in self.enables}
        k = i
        while k < len(st):
            lab = st[k].label
            if lab and (lab in open_labels or lab in self.targets):
                for en in self.enables:
                    if en["label"] == lab:
                        en["live"] = False      # nothing runs into this label from above
                        en["exit"] = exit_path
                return k
            k += 1
        if self.enables:
            # open sections whose label is in another network: the rest of the network never runs
            pass
        self.ended_network = True
        return len(st)

    def jump(self, st: List[Stmt], i: int) -> int:
        s = st[i]
        target = next((k for k in range(i + 1, len(st)) if st[k].label == s.arg), None)
        name = {"JU": "SPA", "JC": "SPB", "JCN": "SPBN", "JNB": "SPBNB", "JCB": "SPBB"}.get(s.op, s.raw) \
            if self.de else s.op
        if target is None:
            # jump to another network (LAD: jump coil, the target network gets the label)
            c = None if s.op == "JU" else self.rlo()
            cond = None if s.op == "JU" else (c if s.op in ("JC", "JCB") else _negate(c))
            self.emit(cond, Output("JMP", [s.arg, f"({name})"]), "jump")
            if s.op == "JU":
                return self.skip_dead(st, i + 1)
            self.after_output(c)
            return i + 1
        if s.op == "JU":
            if self.enables:
                top = self.enables[-1]
                pos = {st[k].label: k for k in range(i + 1, len(st)) if st[k].label}
                l1 = pos.get(top["label"])
                if l1 is not None and target > l1:
                    # if / else:  SPB L1 / <else> / SPA L2 / L1: <then> / L2: ...
                    for en in self.enables[:-1]:
                        lk = pos.get(en["label"])
                        if lk is not None and lk < target:
                            raise Unsupported("jumps that cross each other")
                    self.enables.insert(len(self.enables) - 1, {
                        "label": s.arg, "enable": _negate(top["enable"]), "depth": top["depth"],
                        "acc_version": top["acc_version"], "live": True})
                    return self.skip_dead(st, i + 1, exit_path=False)
            return self.skip_dead(st, i + 1)
        c = self.rlo()
        enable = c if s.op in ("JNB", "JCN", "JNBI") else _negate(c)
        self.enables.append({"label": s.arg, "enable": enable, "depth": len(self.levels),
                             "acc_version": self.acc_version, "live": True})
        self.lv.fc, self.lv.cached = True, None
        return i + 1

    # ---- one instruction
    def addr(self, arg: str) -> str:
        """DBX1.0 after 'AUF DB10' -> DB10.DBX1.0"""
        if self.open_db and (re.match(r"^DB[XBWD]\d", arg) or arg in ("DBLG", "DBNO")):
            return f"{self.open_db}.{arg}"
        if self.open_di and re.match(r"^DI[XBWD]\d", arg):
            return f"{self.open_di}.{arg}"
        return arg

    def acc_load(self, entry):
        self.acc2, self.acc1 = self.acc1, entry
        self.acc_version += 1

    def acc_set(self, entry):
        self.acc1 = entry
        self.acc_version += 1

    def step(self, st: List[Stmt], i: int) -> int:
        s = st[i]
        op = s.op
        arg = self.addr(s.arg) if op in ADDR_OPS else s.arg
        nxt = st[i + 1] if i + 1 < len(st) else None

        if op in SKIP_OPS:
            return i + 1
        # ENO plumbing STEP 7 adds after boxes: "UN OV / SAVE / CLR" and "SET / SAVE / CLR"
        if op in ("A", "AN") and arg in OV_OPERANDS and nxt is not None and nxt.op == "SAVE":
            return self._skip_save(st, i + 2)
        if op == "SET" and nxt is not None and nxt.op == "SAVE":
            return self._skip_save(st, i + 2)

        if op in ("A", "AN", "O", "ON", "X", "XN") and arg:
            if arg in BR_OPERANDS and op == "A":
                self.eno()
                return i + 1
            if op == "A" and self.lv.fc and self._timer_inline(arg):
                return i + 1
            self.logic(op.rstrip("N") if op != "ON" else "O", self.contact(op, arg))
            return i + 1
        if op == "O" and not arg:            # AND before OR
            lv = self.lv
            if lv.fc:
                self._check_fresh("O")
                lv.terms, lv.cur, lv.cached, lv.fc = [], None, None, False
            lv.terms.append(lv.cur)
            lv.cur = None
            return i + 1
        if op in ("A(", "AN(", "O(", "ON(", "X(", "XN("):
            if self.lv.fc:
                self._check_fresh(op)
                self.lv.terms, self.lv.cur, self.lv.cached, self.lv.fc = [], None, None, False
                self.lv.fresh = True
            self.levels.append(_Level(op))
            return i + 1
        if op == ")":
            if len(self.levels) == 1:
                raise Unsupported(") without (")
            lv = self.levels.pop()
            val = lv.value() if not lv.fc else (lv.cached if lv.cached is not None else lv.value())
            if lv.op in ("AN(", "ON(", "XN("):
                val = _negate(val)
            parent = self.lv
            if parent.cur is None and not parent.terms and getattr(parent, "fresh", False):
                parent.cur = val
            elif lv.op in ("A(", "AN("):
                if parent.cur is not None and _contains_box(val):
                    val = self._hoist(val)
                parent.cur = series(parent.cur, val) if parent.cur is not None else val
            elif lv.op in ("X(", "XN("):
                parent.cur = _xor(parent.cur, val)
            else:
                parent.cur = parallel(parent.cur, val)
            parent.fc = False
            parent.fresh = False
            return i + 1
        if op in ("FP", "FN"):
            self.logic("A", Contact(arg, "P" if op == "FP" else "F"))
            return i + 1
        if op == "NOT":
            lv = self.lv
            val = self.rlo()
            lv.terms, lv.cur, lv.fc = [], _negate(val), False
            return i + 1
        if op == "SET":                      # RLO = 1
            lv = self.lv
            lv.terms, lv.cur, lv.cached, lv.fc = [], None, None, False
            self.rlo_dirty = False
            return i + 1
        if op == "CLR":                      # RLO = 0
            lv = self.lv
            lv.terms, lv.cur, lv.cached, lv.fc = [], Contact("", "BOX", "0"), None, False
            self.rlo_dirty = False
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
            self.acc_load(("bcd" if op == "LC" else "val", arg))
            return i + 1
        if op in ARITH:
            self.acc_set(("op", ARITH[op], self._acc_text(self.acc2), self._acc_text(self.acc1)))
            return i + 1
        if op in ("+", "INC", "DEC"):
            name = {"+": "ADD", "INC": "INC", "DEC": "DEC"}[op]
            self.acc_set(("op", name, self._acc_text(self.acc1), arg))
            return i + 1
        if op in WORD_LOGIC:
            if arg:
                self.acc_set(("op", WORD_LOGIC[op], self._acc_text(self.acc1), arg))
            else:
                self.acc_set(("op", WORD_LOGIC[op], self._acc_text(self.acc2), self._acc_text(self.acc1)))
            return i + 1
        if op in SHIFTS:
            count = arg if arg else self._acc_text(self.acc2)
            self.acc_set(("op", SHIFTS[op], self._acc_text(self.acc1), count))
            return i + 1
        if op in UNARY:
            self.acc_set(("op1", UNARY[op], self._acc_text(self.acc1)))
            return i + 1
        if op == "T":
            self.transfer(arg)
            return i + 1
        if op in ("AUF", "OPN"):
            if arg.startswith("DI"):
                self.open_di = arg
            else:
                self.open_db = arg
            return i + 1

        # ---- jumps, calls, block end
        if op in COND_JUMPS or op == "JU":
            return self.jump(st, i)
        if op in STATUS_JUMPS:
            raise Unsupported(f"{s.raw}: jump on status bits (result of math) - no ladder equivalent")
        if op in ("UC", "CALL"):
            args = [arg] + (["(" + ", ".join(s.params) + ")"] if s.params else [])
            self.emit(None, Output("CALL", args), "call")
            self.lv.fc, self.lv.cached = True, None
            return i + 1
        if op == "CC":
            c = self.rlo()
            self.emit(c, Output("CALL", [arg]), "call")
            self.after_output(c)
            return i + 1
        if op in ("BE", "BEU"):
            self.emit(None, Output(op if op == "BE" else "BEA" if self.de else "BEU", []), "end")
            if not self.enables:
                nxt_alive = any(x.label in self.targets for x in st[i + 1:] if x.label)
                if not nxt_alive:
                    self.ended = True
                    return len(st)
            return self.skip_dead(st, i + 1)
        if op == "BEC":
            c = self.rlo()
            self.emit(c, Output("BEB" if self.de else "BEC", []), "end")
            self.lv.fc, self.lv.cached = True, None    # not ended: RLO = 1, new chain
            return i + 1
        if op in UNSUPPORTED_WHY:
            raise Unsupported(f"{s.raw}: {UNSUPPORTED_WHY[op]}")
        raise Unsupported(f"{s.raw}: no ladder equivalent")

    def _check_fresh(self, op: str):
        """A new logic chain right after a label two ways lead to: U is fine (RLO is 1 on the jump
        path), anything that combines with the old RLO is not."""
        if self.rlo_dirty == "chain":
            raise Unsupported("logic result depends on which jump was taken")
        self.rlo_dirty = False

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
                or e.depth != len(self.levels) or self.enables or self.rlo_dirty:
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
            raise Unsupported("uses an accumulator value loaded in another network")
        if a[0] == "dirty":
            raise Unsupported("accumulator value depends on which jump was taken")
        if a[0] == "op":
            return f"{a[1]}({a[2]}, {a[3]})"
        if a[0] == "op1":
            return f"{a[1]}({a[2]})"
        return a[1]

    def transfer(self, dest: str):
        a = self.acc1
        self._acc_text(a)      # raises if unknown
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
        if a[0] in ("op", "op1"):
            name, ins = a[1], list(a[2:])
            if last is not None and last.kind == "acc" and last.out.op == name \
                    and last.out.args[:len(ins)] == ins and last.depth == len(self.levels):
                last.out.args.append(dest)
                return
            self.emit(None, Output(name, ins + ["→", dest]), "acc")
        else:
            if last is not None and last.kind == "acc" and last.out.op == "MOVE" and last.out.args[0] == src \
                    and last.depth == len(self.levels):
                last.out.args.append(dest)
                return
            self.emit(None, Output("MOVE", [src, "→", dest]), "acc")

    def eno(self):
        """U BIE after a box: continue the rung from the box's ENO (box drawn in series)."""
        self.rlo_dirty = False
        boxes = []
        k = len(self.entries)
        while k > 0 and self.entries[k - 1].kind in ("acc", "call") and self.entries[k - 1].depth == len(self.levels):
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


def _plain_contacts(e):
    items = e.items if hasattr(e, "items") else [e]
    return [i for i in items if isinstance(i, Contact) and i.kind in ("NO", "NC") and i.box is None]


def _strip_known(cond, known):
    """Drop contacts already required by the enclosing jump section (A·A = A)."""
    if cond is None or not known:
        return cond
    items = cond.items if hasattr(cond, "items") else [cond]
    keep = [i for i in items if not (isinstance(i, Contact) and i.kind in ("NO", "NC")
                                     and i.box is None and any(i == k for k in known))]
    if len(keep) == len(items):
        return cond
    if not keep:
        return None
    return keep[0] if len(keep) == 1 else Series(keep)


def _flat_children(entries: List[_Entry], depth: int, known: list) -> list:
    """Children of a branch point: outputs, or branches for each condition / nested section.
    Consecutive outputs with the same condition share one branch."""
    kids: list = []
    prev_raw, prev_branch = object(), None
    k = 0
    while k < len(entries):
        e = entries[k]
        if len(e.ens) > depth:
            en = e.ens[depth]
            j = k
            while j < len(entries) and len(entries[j].ens) > depth and entries[j].ens[depth] is en:
                j += 1
            inner_known = known + _plain_contacts(en)
            kids.append(Branch(_strip_known(en, known), _flat_children(entries[k:j], depth + 1, inner_known)))
            prev_raw, prev_branch = object(), None
            k = j
            continue
        raw = _strip_known(e.raw, known)
        if raw is None:
            kids.append(e.out)
            prev_raw, prev_branch = object(), None
        elif e.raw is prev_raw and prev_branch is not None:
            prev_branch.children.append(e.out)
        else:
            prev_branch = Branch(raw, [e.out])
            kids.append(prev_branch)
            prev_raw = e.raw
        k += 1
    return kids


def _rungs_from_entries(entries: List[_Entry]) -> List[Branch]:
    """One rung per logic chain; a forward jump section (SPB/SPBN/SPBNB ... label) becomes one rung
    whose condition is drawn once, with the section's outputs branching off it."""
    roots: List[Branch] = []
    k = 0
    while k < len(entries):
        e = entries[k]
        if e.out.inputs:                              # counters / timers with R: own rung
            roots.append(Branch(None, [e.out]))
            k += 1
            continue
        if e.ens:
            en = e.ens[0]
            j = k
            while j < len(entries) and entries[j].ens and entries[j].ens[0] is en \
                    and not entries[j].out.inputs:
                j += 1
            roots.append(Branch(None, [Branch(en, _flat_children(entries[k:j], 1, _plain_contacts(en)))]))
            k = j
            continue
        j = k + 1
        while j < len(entries) and not entries[j].ens and not entries[j].out.inputs \
                and entries[j].cond is e.cond:
            j += 1
        outs = [x.out for x in entries[k:j]]
        roots.append(Branch(None, [Branch(e.cond, outs)] if e.cond is not None else outs))
        k = j
    return roots


JUMP_OPS = COND_JUMPS | STATUS_JUMPS | {"JU"}


def _label_info(blk: Block):
    """targets: labels any jump goes to. external: labels inside a network (not its first
    instruction) that are reached from another network or by a backward jump."""
    where = {}
    for n, net in enumerate(blk.networks):
        for k, st in enumerate(net.stmts):
            if st.label:
                where[st.label] = (n, k)
    targets, external = set(), set()
    for n, net in enumerate(blk.networks):
        in_list = False
        for k, st in enumerate(net.stmts):
            is_jump = st.op in JUMP_OPS or st.op == "JL" or (in_list and st.op == "JU")
            in_list = st.op == "JL" or (in_list and st.op == "JU")
            if not is_jump or st.arg not in where:
                continue
            targets.add(st.arg)
            tn, tk = where[st.arg]
            if tk > 0 and (tn != n or tk <= k):
                external.add(st.arg)
    return targets, external


def _stl_rung(net: Network, why: str) -> Branch:
    return Branch(None, [Output("STL", [f"{len(net.stmts)} instructions"])])


def convert_text(text: str) -> List[Rung]:
    blocks, lang = parse_awl(text)
    rungs: List[Rung] = []
    for blk in blocks:
        targets, external = _label_info(blk)
        title = f"{blk.name}" + (f" · {blk.title}" if blk.title else "")
        skipping, skipped = False, []
        for net in blk.networks:
            if not net.stmts:
                continue
            first_label = net.stmts[0].label
            if skipping:
                if first_label in targets:
                    skipping = False        # a jump leads here: the network runs after all
                else:
                    skipped.append(net)
                    continue
            head = f"Network {net.index}" + (f": {net.title}" if net.title else "")
            nb = NetworkBuilder(net, lang, targets, external)
            stl_why = ""
            warns: List[str] = []
            try:
                roots = _rungs_from_entries(nb.run())
                warns = list(nb.warnings)
            except Unsupported as ex:
                roots, stl_why = [_stl_rung(net, str(ex))], str(ex)
            except Exception as ex:  # a converter bug must not lose the network
                roots = [_stl_rung(net, "")]
                stl_why = f"converter error ({type(ex).__name__}: {ex})"
                warns = [f"converter error ({type(ex).__name__}: {ex}) - please report this network"]
            if not roots:
                continue
            for k, root in enumerate(roots):
                r = Rung(root, None, len(rungs), first_label + ":" if (k == 0 and first_label) else "")
                r.block, r.block_title, r.network = blk.name, title, head
                r.source = list(net.stmts) if k == 0 else []
                r.warnings = warns if k == 0 else []
                r.stl_only = stl_why if k == 0 else ""
                rungs.append(r)
            if nb.ended:
                skipping = True
        if skipped and any(st.op not in ("BE", "BEU", "NOP", "BLD") for n in skipped for st in n.stmts):
            last = next((r for r in reversed(rungs) if r.block == blk.name), None)
            if last is not None:
                last.warnings = last.warnings + [
                    f"{len(skipped)} network(s) after the block end (BE) never run - not drawn"]
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
