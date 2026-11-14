# Ladder conversion

Turns PLC **instruction-list code** (the text form of a PLC program) into **ladder diagrams** you can read.
You give it the program as a printed PDF, a text file or a CSV export. It produces an HTML page with one
ladder drawing per rung, or a text version in the terminal.

Useful when the only copy of a program is a printout in instruction-list form (Mitsubishi "list",
Panasonic "Boolean", ...) and you want to read or check it as ladder, or rewrite it on another PLC.

```
 ST   X 40                     |   X40       R130F
 OR   R 401          ──►       |---| |---+---|/|-----------( R401 )---|
 AN/  R 130F                   |   R401  |
 OT   R 401                    |---| |---+
```

## Supported PLCs

The dialect is detected automatically.

| Vendor | Software / view | Status |
|---|---|---|
| **Mitsubishi** FX / A / Q | GX Developer, GX Works (list mode) | ✅ |
| **Panasonic** FP0 / FP0R / FP-X / FP2 ... | FPWIN GR ("Boolean" view) | ✅ tested on a real FP0R program (430 rungs) |
| **Omron** CS / CJ / CP / C-series | CX-Programmer (mnemonics) | planned, see [docs/ROADMAP.md](docs/ROADMAP.md) |
| **Siemens** S7-300/400 | STEP 7 v5.x STL, `.awl` source (German or English mnemonics) | ✅ tested on a real program (10 blocks, 325 rungs) |

## Install

Needs **Python 3.8+**. PDF input also needs `pypdf`:

```bash
git clone https://github.com/gzizouseif24/Ladder-conversion.git
cd Ladder-conversion
pip install -r requirements.txt
```

## Usage

**Siemens:** don't print. Export the whole program as one text file:
in SIMATIC Manager double-click any block (e.g. OB1) → in the LAD/STL/FBD editor **File → Generate Source…** →
save it in the *Sources* folder → select all blocks in the dialog (click first, Shift+click last, **→**) → OK.
Then in SIMATIC Manager right-click the new source in *Sources* → **Export Source…** → save as `program.awl`, and run
`python -m ladder input/program.awl`. Skip to step 4 below.

**Mitsubishi / Panasonic / other printed programs:**

1. Print the program from the PLC software **in instruction-list view** (not ladder view) to PDF.
   Use a "print to PDF" printer, not a scan: the PDF must contain text.
2. Put the PDF in the `input/` folder. Git ignores it, so customer programs are never committed.
3. Check what was read from the PDF:

   ```bash
   python -m ladder input/program.pdf --list
   ```

   You should see one line per instruction: step number, instruction and operands.
   If instructions are missing or garbled, the print layout is new to the tool. Open an issue with one page of it.
4. Convert:

   ```bash
   python -m ladder input/program.pdf
   ```

   This writes `input/program.html`. Open it in a browser. Every rung shows its step number, the drawing,
   any warnings, and a collapsible copy of its instruction list for checking.

Other options:

```bash
python -m ladder input/program.pdf --text           # text ladder in the terminal
python -m ladder input/program.pdf -o out.html      # choose the output file
python -m ladder examples/sample_fx.txt             # plain text input (one instruction per line)
python -m ladder program.csv                        # GX Developer / GX Works CSV export
python -m ladder input/plant/                       # a folder: every .pdf/.awl/.txt/.csv in it, one page
```

The HTML page has an index of blocks at the top (Siemens OB/FC/FB, or one entry per file for a folder).
`CALL FC1` boxes link to that block.

## Reading the output

| Drawing | Meaning |
|---|---|
| `-| |-` | normally-open contact |
| `-|/|-` | normally-closed contact |
| `-|P|-` / `-|F|-` (arrows in HTML) | rising / falling edge contact (LDP, LDF ...) |
| `[DF]` `[DF/]` `[MEP]` `[MEF]` | edge of the result so far (Panasonic DF, Mitsubishi MEP/MEF) |
| `[>= DT100 K5]` | comparison contact |
| `( Y0 )` | output coil (OUT / OT) |
| `[SET M0]` `[MOV K100 D0]` `[TML 1003 K10]` | any other output or function instruction, shown as a box with its operands |
| tall box with `up/down` `count` `reset` | instruction with several inputs (Panasonic F118 UDC, KP, CT, SR) |
| rung label (e.g. `P0`, `SUB 1`) | jump pointer / subroutine / label before the rung |
| `[MOVE 0 → MW150 MW152]` `[MUL_DI a b → c]` | Siemens box with EN/ENO. A box *inside* the rung passes ENO to the right |
| `[S_EVERZ T20 S5T#3S]` inside the rung | Siemens timer box: input on the left = S, output on the right = Q. German names (S_EVERZ = S_ODT on-delay, S_AVERZ = S_OFFDT off-delay ...) |
| tall box `ZV` `ZR` `R` | Siemens counter (Z_VORW / Z_RUECK / ZAEHLER), one input row per pin. `DEZ→MW230` / `DUAL→` are its outputs |
| `[>=I MW230 3]` | Siemens comparison in the rung |
| `[JMP M001 (SPB)]` | jump to the network labelled `M001:` |
| `STL` box + ⚠ | Siemens network that can't be drawn as ladder (e.g. TAK, indirect addressing): read its instruction list |
| ⚠ warning under a rung | the instruction list did something that could not be drawn exactly. Check that rung by hand |

Two things look different from the vendor editor on purpose:

- **OR / ORS right after an output.** Some PLCs let you keep building on the result after an output,
  e.g. `ST T1006 / OT R1332 / OR R1366 / OT R1571`. Ladder can't draw that, so the contacts before the
  first output are **drawn again** in parallel (`T1006` appears twice). The logic is the same.
- **Siemens timer used in the middle of a condition** (`U E1.0 / U( U E1.5 / SE T9 / U T9 ) / = M13.0`):
  the timer gets its own rung (`E1.5 → T9`) and the main rung uses contact `T9`, so the drawing never
  suggests that `E1.0` feeds the timer. Same for boxes chained after other contacts.
- **New logic inside an MPS/PSHS section.** An `LD`/`ST` from the left rail between `PSHS` and `POPS` is
  drawn as its own line in the same rung.

## How it works

```
 PDF / txt / csv ──► reader ──► instructions ──► builder ──► rung trees ──► layout ──► grid ──► text / HTML+SVG
```

| File | Job |
|---|---|
| `ladder/pdf_reader.py` | Pulls instructions out of a printed PDF: skips headers and footers, merges operands printed on the next line, keeps comments separate |
| `ladder/reader.py` | Text and CSV input. Chooses the right reader |
| `ladder/dialects.py` | Vendor instruction sets, mapped onto one common set (e.g. Panasonic `ST` = Mitsubishi `LD`), plus dialect detection |
| `ladder/siemens.py` | Siemens STL: parses the `.awl` (blocks, networks), evaluates the RLO with brackets and jumps, and turns the code STEP 7 generates for LAD/FBD boxes back into boxes |
| `ladder/builder.py` | Runs the instruction list like the PLC does (block stack for ANB/ORB, branch stack for MPS/MRD/MPP) and builds a tree per rung |
| `ladder/model.py` | The tree: contacts in series/parallel, branch points, outputs |
| `ladder/layout.py` | Places the tree on a fixed grid like the vendor ladder editors, with outputs aligned on the right |
| `ladder/render_text.py`, `ladder/render_svg.py` | Draw the grid as text or as SVG in an HTML page (light and dark mode) |

Every renderer only maps grid cells to characters or pixels, so the geometry is worked out once in `layout.py`
and lines always meet cleanly.

## Supported instructions

**Mitsubishi:** LD/LDI/LDP/LDF/LDPI/LDFI, AND/ANI/ANDP/ANDF..., OR/ORI/ORP/ORF..., ANB, ORB, MPS/MRD/MPP,
INV, MEP/MEF, comparison contacts (`LD= D0 K10`, `ANDD>` ...), STL, pointers (`P0`), OUT/SET/RST/PLS/PLF,
timers and counters with set values, and any applied instruction (MOV, CMP, CJ ...).
END/FEND/RET get their own rung.

**Panasonic:** ST/ST/, AN/AN/, OR/OR/, ANS/ORS, PSHS/RDS/POPS, DF/DF//DFI, NOT, comparison contacts
(`ST >=`, `STD<=` ...), OT/SET/RST/KP, timers TML/TMR/TMX/TMY, CT, SR, any `F`/`P` instruction with its
operands (`F0 (MV)`, `F118 (UDC)` ...), ED/CNDE, labels (LBL, SUB, INT, SSTP). NOP is skipped.

**Siemens STL (German/English):** U/A, UN/AN, O, ON, O alone, U(/A( … ), FP/FN, NOT, =, S, R, all S5 timers
(SI/SV/SE/SS/SA = SP/SE/SD/SS/SF) with R/DUAL/DEZ pins, counters ZV/ZR/S/R/L/LC, comparisons (==I … <=D, R),
L/T and arithmetic (+I … /R), EN/ENO sections (SPBNB … `U BIE`), jumps (SPB, SPBN, SPA …), UC/CC/CALL, BE/BEB.
Not drawn (network shown as STL): TAK, indirect addressing, accumulator juggling, XOR.

**Not yet:** Omron. MC/MCR (master control) drawn as a zone, step-ladder bus drawing, device comments/symbols
next to contacts.

## Development

```bash
python -m unittest          # all tests, no extra dependencies except pypdf for the PDF tests
```

Tests use made-up programs only. Real customer programs go in `input/` and stay out of git.

Adding a PLC dialect: see [docs/ROADMAP.md](docs/ROADMAP.md).
