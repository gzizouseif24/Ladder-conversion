"""Data model for a ladder program.

A rung is a tree:
    Branch(expr, children)
        expr     - the contact logic in front of this branch point (series/parallel tree)
        children - what hangs off the right side of `expr`: outputs or further branches

This shape covers Mitsubishi MPS/MRD/MPP stacks, multiple coils on one rung, and
"continuous output" (OUT Y0 / AND X1 / OUT Y1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union


@dataclass
class Contact:
    device: str
    # NO, NC, P (rising), F (falling), PI, FI (negated pulse), CMP (compare),
    # INV, MEP, MEF, STL
    kind: str = "NO"
    text: str = ""  # extra text, e.g. "= D0 K100" for compare contacts

    @property
    def width(self) -> int:
        return 2 if self.kind == "CMP" else 1


@dataclass
class Series:
    items: list


@dataclass
class Parallel:
    branches: list


Expr = Optional[Union[Contact, Series, Parallel]]


@dataclass
class Output:
    op: str
    args: List[str]
    step: Optional[int] = None

    @property
    def is_coil(self) -> bool:
        return self.op == "OUT"

    @property
    def text(self) -> str:
        if self.is_coil:
            return " ".join(self.args)
        return " ".join([self.op] + self.args)


@dataclass
class Branch:
    expr: Expr = None
    children: list = field(default_factory=list)  # Output | Branch


@dataclass
class Instr:
    op: str
    args: List[str]
    step: Optional[int]
    line: int = 0
    comment: str = ""

    def __str__(self) -> str:
        s = " ".join([self.op] + self.args)
        return f"{s}    ; {self.comment}" if self.comment else s


@dataclass
class Rung:
    root: Branch
    start_step: Optional[int]
    index: int = 0
    label: str = ""
    source: List[Instr] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def series(a: Expr, b: Expr) -> Expr:
    if a is None:
        return b
    if b is None:
        return a
    items = (a.items if isinstance(a, Series) else [a]) + (b.items if isinstance(b, Series) else [b])
    return Series(items)


def parallel(a: Expr, b: Expr) -> Expr:
    if a is None:
        return b
    if b is None:
        return a
    br = (a.branches if isinstance(a, Parallel) else [a]) + (b.branches if isinstance(b, Parallel) else [b])
    return Parallel(br)
