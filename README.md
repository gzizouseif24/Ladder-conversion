# Ladder conversion

Converts PLC instruction-list code into ladder diagrams you can view.
The first supported dialect is **Mitsubishi (GX Developer / GX Works, FX/A/Q series)**.

```bash
python3 -m ladder examples/sample_fx.txt              # writes examples/sample_fx.html
python3 -m ladder examples/sample_fx.txt --text       # text ladder in the terminal
python3 -m ladder program.pdf --list                  # show instructions read from a PDF
python3 -m unittest                                   # tests (no dependencies)
```

## Input
- **Printed PDF** from the vendor software (print the *list* view, not the ladder view, to PDF).
  Needs `pip install pypdf`. Run with `--list` first to check what was read from the PDF.
  Scanned PDFs (images) have no text and are not supported yet.
- Plain text, one instruction per line (`LD X000`, optionally prefixed with a step number; `;` starts a comment).
- GX Developer / GX Works CSV exports from list mode (comma or tab separated, UTF-8/UTF-16/Shift-JIS).

## How it works
1. `reader.py` – reads the instruction list.
2. `builder.py` – simulates the block stack (LD/ANB/ORB) and branch stack (MPS/MRD/MPP) to build a
   tree per rung: series/parallel contact logic plus the outputs that hang off it.
3. `layout.py` – places the tree on a fixed grid, like GX's ladder editor (outputs aligned on the right).
4. `render_text.py` / `render_svg.py` – draw the grid as text or as an HTML page with one SVG per rung.

## Supported instructions
LD/LDI/LDP/LDF/LDPI/LDFI, AND/ANI/ANDP/ANDF…, OR/ORI/ORP/ORF…, ANB, ORB, MPS/MRD/MPP, INV, MEP/MEF,
compare contacts (`LD= D0 K10`, `ANDD>`, …), STL, pointers (`P0`), and any output/applied instruction
(OUT, SET, RST, PLS, MOV, CJ, timers/counters with set values, …). END/FEND/RET… get their own rung.

Not yet: MC/MCR nesting display, STL step-ladder bus drawing, device comments.
