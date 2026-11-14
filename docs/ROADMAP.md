# Roadmap: next phase (Omron + Siemens)

## Where we are

Mitsubishi and Panasonic work end to end (PDF → ladder HTML). Both are **stack-based** instruction lists:
a new `LD`/`ST` opens a logic block, `ANB`/`ORB` join blocks, `MPS`/`POPS` save branch points. The
builder simulates that stack. The other dialects are mapped onto Mitsubishi names through an alias table.

- **Omron** is the same kind of language, so it fits the current design with an alias table and a reader.
- **Siemens STL** is not stack-based in the same way. It works on an RLO (result of logic operation) with
  parentheses, networks, an accumulator and jumps. It needs its own builder that produces the same rung
  trees, so layout and drawing can stay shared.

---

## Step 1: Restructure into dialect modules (small, do first)

Today `dialects.py` holds aliases and `pdf_reader.py` has a Mitsubishi branch and a Panasonic branch.
Before adding two more vendors, give each dialect its own module with the same interface:

```
ladder/dialects/
    base.py          # Dialect interface
    mitsubishi.py
    panasonic.py
    omron.py
    siemens.py
```

```python
class Dialect:
    name: str
    def score(self, text) -> float           # how likely this text is this dialect (for auto-detect)
    def parse_lines(self, lines) -> list[Instr]   # print/text lines -> instructions
    def build(self, instrs) -> list[Rung]        # instructions -> rung trees (stack builder or own)
```

- Mitsubishi, Panasonic and Omron share the stack builder (`builder.py`) and differ only in reader and alias table.
- Siemens plugs in its own builder.
- Add `--dialect mitsubishi|panasonic|omron|siemens` to override auto-detection.
- **Several input files / a folder** → one HTML page. Siemens prints one PDF per block (OB1, FC1, ...),
  so `python -m ladder input/plant/` reads every PDF/AWL/TXT in the folder, gives each block its own
  section with an index at the top (OB first, then FB, FC), and turns `CALL FC1` into a link.
- **Done when:** all current tests pass unchanged and the BVM793-26 output is identical.

## Step 2: Omron (CX-Programmer mnemonics)

**Instruction set → canonical (Mitsubishi) names**

| Omron | Canonical | Notes |
|---|---|---|
| `LD`, `LD NOT`, `AND`, `AND NOT`, `OR`, `OR NOT` | LD, LDI, AND, ANI, OR, ORI | **two-word mnemonics**: the reader must join `LD NOT` |
| `AND LD`, `OR LD` | ANB, ORB | two words again |
| `OUT`, `OUT NOT` | OUT, OUT with an inverted coil | |
| `OUT TR0` … `LD TR0` | branch save / restore (like MPS/MRD) | **TR temporary relays**: map `OUT TRn` → save a branch point, `LD TRn` → return to it |
| `TIM n #100`, `TIMH`, `TTIM` | timer box | set value on the same line or the next |
| `CNT n #10`, `CNTR` | box with 2 inputs (count, reset) | 3 inputs for CNTR |
| `KEEP(011)` | box with 2 inputs (set, reset) | |
| `DIFU(013)`, `DIFD(014)` | output box | an output that makes a one-scan pulse bit, not an edge contact |
| `UP(521)`, `DOWN(522)` | edge of the result (like MEP/MEF) | CJ/CS/CP |
| `@MOV`, `%MOV` | box marked ↑ / ↓ | instruction prefixes for "execute on rising/falling edge" |
| `!LD` | contact marked immediate | immediate refresh prefix |
| `LD =`, `AND >=`, `=(300)` ... | comparison contacts | symbol comparison instructions |
| `MOV(021)`, `CMP(020)`, any `NAME(nnn)` | box with operands | function code in brackets |
| `SET`, `RSET` | SET/RST | |
| `IL(002)` / `ILC(003)`, `JMP` / `JME` | zone start/end labels | same as MC/MCR, drawn as labelled rungs first |
| `END(001)` | END | |

**Addresses** (no conversion, just recognised as operands):
`0.00`, `1.05`, `W10.03`, `H0.00`, `A200.11`, `T0000`, `C0001`, `D100`, `#0100` (hex constant),
`&100` (decimal constant), `+5`, `TR0`. The old C-series uses 5-digit bits such as `00000`, `01005`, `HR 00`.

**Reader work**
- CX-Programmer prints mnemonics as a table: Step, Instruction, Operand(s), Comment. Operands can be in
  several columns on the same line. A real print is needed to fix the column handling.
- Treat `NOT`, `LD`, `TR` as continuation words of the mnemonic, not operands.

**Tests:** synthetic program with every instruction above, plus a real CX-Programmer print in `input/`.

**Done when:** a real Omron program converts with no unexplained warnings and spot-checked rungs match
the CX-Programmer ladder view.

## Step 3: Siemens STL, bit logic (S7-300/400 first, then S5)

Siemens programs are split into **blocks** (OB, FC, FB) and **networks**. A network is not always one
rung, but rungs never cross networks, which helps.

**Input**
- **Best:** STEP 7 v5.x LAD/STL editor → *File → Generate Source…* → select all blocks → **one `.AWL`
  text file for the whole program** (LAD blocks are written as STL too). TIA Portal (S7-300/400):
  right-click blocks → *Generate source from blocks*. Clean text, has block and network headers,
  symbols and comments. Know-how-protected blocks can't be exported (or printed).
- **Also:** PDF prints of the STL editor, one PDF per block, read as a folder (see Step 1).
- **Mnemonics:** English (A, AN, O, =, JC, L, T) **and German** (U, UN, O, =, SPB, L, T). An alias table maps German to English.

**New builder: RLO with parentheses**

| STL | Meaning | Ladder |
|---|---|---|
| first `A`/`AN`/`O`/`ON` after `=`/`S`/`R` or at the start of a network | starts a new logic chain (first check) | new rung, or new branch from the rail |
| `A x` / `AN x` | AND | series contact |
| `O x` / `ON x` | OR | parallel contact |
| `O` alone | "AND before OR": `A a A b O A c A d` = (a·b) + (c·d) | two parallel series paths |
| `A(` `O(` `AN(` `ON(` … `)` | nested expression (a stack of up to 7 levels) | parallel/series block, `AN(` = block followed by a NOT |
| `X` / `XN` / `X(` | exclusive OR | drawn as a small XOR box, or as two branches |
| `NOT` | invert the RLO | NOT contact (like Mitsubishi INV) |
| `=` `S` `R` | coil / set / reset; the RLO stays, so several `=` in a row are parallel coils | outputs on the same branch point |
| `FP M10.0` / `FN` | edge using a memory bit | `-|P|-` contact with its memory bit |
| `SET` / `CLR` / `SAVE` | RLO = 1 / 0 / save to BR | rare. Draw as text boxes, flag |

The output is the same `Rung` / `Branch` / `Series` / `Parallel` tree, so `layout.py` and the renderers
need no change apart from small additions (XOR box, `-|P|-` with a memory bit, NOT contact).

**Addresses:** `I 0.0` / `E 0.0`, `Q 4.1` / `A 4.1`, `M 10.3`, `DB10.DBX2.0`, `T 5`, `C 3`,
symbols in quotes (`"Motor_On"`), locals (`#Start`). Show the symbol under the address when known.

## Step 4: Siemens STL, word logic, timers, jumps

Everything that uses the accumulator is turned into the LAD boxes Siemens would draw, using
pattern matching on short sequences:

| STL pattern | LAD box |
|---|---|
| `L a` / `T b` | MOVE (IN a, OUT b) |
| `L a` / `L b` / `+I` / `T c` (also -I *I /I, R and D variants) | ADD_I / SUB_I ... |
| `L a` / `L b` / `==I` (also <>I >I <I >=I <=I, R, D) | comparison contact `CMP ==I` in the rung |
| `A x` / `L S5T#2S` / `SD T5` (SP SE SS SF) | S_ODT/S_PULSE/... timer box. `R T5` on the next lines is the reset pin, `L T5` / `T MW..` is the BI output |
| `CU C3` / `CD C3` / `S C3` / `R C3` | S_CUD counter box with pins |
| `JC label` / `JCN` / `JU` | `(JMP)` / `(JMPN)` coil to a label, and the target network gets the label |
| `CALL FC10`, `UC`, `CC` | call box with parameter list |
| `BE`, `BEC`, `BEU` | end-of-block coil |

Anything that doesn't match a known pattern (indirect addressing, `TAK`, `LOOP`, multi-step accumulator
juggling) is shown as an **STL-only network**: the original instructions in a box with a clear warning,
exactly what STEP 7 does when it can't convert a network. Nothing is guessed silently.

S5 specifics come after S7: `PB/FB/SB/OB` blocks, `SPA/SPB` jumps, `BE`. The bit logic is nearly the
same as S7.

## Cross-cutting improvements (any time)

- **Device comments and symbols** under contacts and coils (Panasonic/Mitsubishi prints can include them;
  Siemens and Omron almost always have them).
- **Cross-reference page:** where each output is written and where each bit is used (useful for migration).
- **Search box and jump-to-step** in the HTML page for big programs.
- **MC/MCR, IL/ILC zones** drawn as bracketed regions instead of labelled rungs.
- **Export** to a format other tools can import (PLCopen XML), if migration becomes the main use.

## What I need from you

For each new dialect:

1. **A real print to PDF** of the instruction-list view (a full program is best, at least 2 to 3 pages).
2. **A screenshot of a few rungs/networks in the vendor's ladder view**, to compare against.
3. Which **software and version** printed it (CX-Programmer 9.x? STEP 7 v5.5? STEP 5?), and for Siemens,
   **English or German** mnemonics, and whether an `.AWL` source export is possible.

## Suggested order

1. Dialect module restructure (small, keeps everything working)
2. Omron (closest to what already works, quick win)
3. Siemens bit logic (biggest new piece: RLO builder)
4. Siemens word logic, timers and jumps (pattern library, grows with real programs)
