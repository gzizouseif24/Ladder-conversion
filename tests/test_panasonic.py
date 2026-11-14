import unittest

from ladder.builder import build
from ladder.model import Branch, Contact, Output, Parallel, Series
from ladder.pdf_reader import parse_lines

# Text as pypdf extracts it from an FPWIN GR "Boolean" print (made-up program).
PRINT = """Boolean                    Program Name: TEST.fp : 01/01/2026 10:00
     0 ST R 9013
     2 F 0 (MV)
K 0
DT 100
     7 ST X   40
     8 OR R  401
     9 AN/ R 130F
    11 OT R  401
    12 OR R   20
    13 ST X    1
    14 DF/
    15 ORS
    16 OT Y    0
    17 ST R    1
    18 ST R    2
    19 ST R    3
    20 F 118(UDC)
K 0
DT 1206
    25 ST >=
DT 100
K 5
    30 PSHS
    31 ST R 9010
    32 F 0 (MV)
DT 1
SV 5
    37 POPS
    38 AN R 1375
    40 TMX       5
SV 5
    44 OT R F
    45 ED
    46 NOP
FP0R 16K                    Author:     Page 1
"""


class PanasonicTests(unittest.TestCase):
    def setUp(self):
        self.ins, self.skipped = parse_lines(PRINT.splitlines())
        self.rungs = build(self.ins)

    def test_reading(self):
        got = [str(i) for i in self.ins]
        self.assertEqual(got[:6], ["ST R9013", "F0(MV) K0 DT100", "ST X40", "OR R401", "AN/ R130F", "OT R401"])
        self.assertIn("F118(UDC) K0 DT1206", got)
        self.assertIn("ST>= DT100 K5", got)
        self.assertIn("TMX 5 SV5", got)
        self.assertIn("OT RF", got)
        self.assertEqual(len(self.skipped), 2)  # page header + footer

    def test_rungs(self):
        r0, r1, r2, r3, r4 = self.rungs
        self.assertEqual(r0.root.children[0].children, [Output("F0(MV)", ["K0", "DT100"], 2)])
        # self-holding; then OR after the output redraws the path in parallel, ORS with X1 DF/
        self.assertEqual(r1.root.children[0].expr,
                         Series([Parallel([Contact("X40"), Contact("R401")]), Contact("R130F", "NC")]))
        y0 = r1.root.children[1]
        self.assertEqual(y0.children, [Output("OT", ["Y0"], 16)])
        self.assertEqual(len(y0.expr.branches), 3)
        # F118 UDC: three inputs
        udc = r2.root.children[0]
        self.assertEqual(udc.inputs, [Contact("R1"), Contact("R2"), Contact("R3")])
        self.assertEqual(udc.input_names, ["up/down", "count", "reset"])
        # PSHS / ST R9010 / F0 / POPS: R9010 line drawn from the rail inside the same rung
        self.assertEqual(r3.root.children[0].expr, Contact("", "CMP", ">= DT100 K5"))
        self.assertEqual(r3.root.children[1], Branch(Contact("R9010"), [Output("F0(MV)", ["DT1", "SV5"], 32)]))
        self.assertEqual(r3.warnings, [])
        tmx_branch = r3.root.children[0].children[0]
        self.assertEqual(tmx_branch.expr, Contact("R1375"))
        self.assertEqual([o.op for o in tmx_branch.children], ["TMX", "OT"])  # OT R F continues the rung
        self.assertEqual(tmx_branch.children[1].args, ["RF"])
        self.assertEqual(r4.root.children, [Output("ED", [], 45)])


if __name__ == "__main__":
    unittest.main()
