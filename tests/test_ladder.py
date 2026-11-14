import unittest

from ladder.builder import build
from ladder.layout import layout_all
from ladder.model import Branch, Contact, Output, Parallel, Series
from ladder.reader import parse_csv, parse_text
from ladder.render_text import render


def rungs_of(src):
    return build(parse_text(src))


class ReaderTests(unittest.TestCase):
    def test_text_with_and_without_steps(self):
        ins = parse_text("0 LD X000\nOUT T0 K50 ; comment\n\n")
        self.assertEqual([(i.op, i.args, i.step) for i in ins],
                         [("LD", ["X000"], 0), ("OUT", ["T0", "K50"], None)])

    def test_gx_csv_merges_operand_rows(self):
        csv = ('"PLC program"\n"Step No.","Line Statement","Instruction","I/O(Device)","Blank","PI Statement","Note"\n'
               '"0","","LD","M8000","","",""\n"1","","MOV","K100","","",""\n'
               '"","","","D0","","",""\n"6","","END","","","",""\n')
        ins = parse_csv(csv)
        self.assertEqual([(i.op, i.args, i.step) for i in ins],
                         [("LD", ["M8000"], 0), ("MOV", ["K100", "D0"], 1), ("END", [], 6)])


class BuilderTests(unittest.TestCase):
    def test_self_holding(self):
        (r,) = rungs_of("LD X0\nOR Y0\nANI X1\nOUT Y0")
        node = r.root.children[0]
        self.assertEqual(node.expr, Series([Parallel([Contact("X0"), Contact("Y0")]), Contact("X1", "NC")]))
        self.assertEqual(node.children, [Output("OUT", ["Y0"], None)])

    def test_orb_anb(self):
        (r,) = rungs_of("LD X1\nLD X2\nORB\nLD X3\nOR X4\nANB\nOUT Y1")
        self.assertEqual(r.root.children[0].expr,
                         Series([Parallel([Contact("X1"), Contact("X2")]),
                                 Parallel([Contact("X3"), Contact("X4")])]))

    def test_mps_branches(self):
        (r,) = rungs_of("LD X0\nMPS\nAND X1\nOUT Y1\nMRD\nOUT Y2\nMPP\nANI X3\nOUT Y3")
        node = r.root.children[0]
        self.assertEqual(node.expr, Contact("X0"))
        self.assertEqual(len(node.children), 3)
        self.assertEqual(node.children[0], Branch(Contact("X1"), [Output("OUT", ["Y1"], None)]))
        self.assertEqual(node.children[1], Output("OUT", ["Y2"], None))
        self.assertEqual(r.warnings, [])

    def test_new_rung_on_ld_after_output(self):
        self.assertEqual(len(rungs_of("LD X0\nOUT Y0\nLD X1\nOUT Y1")), 2)

    def test_ld_anb_after_output_stays_in_rung(self):
        (r,) = rungs_of("LD X0\nOUT Y0\nLD X1\nOR X2\nANB\nOUT Y1")
        node = r.root.children[0]
        self.assertEqual(node.children[1].expr, Parallel([Contact("X1"), Contact("X2")]))

    def test_compare_pulse_pointer_end(self):
        rs = rungs_of("LD= D0 K10\nOUT Y0\nP0\nLDP X1\nSET M0\nEND")
        self.assertEqual(rs[0].root.children[0].expr, Contact("", "CMP", "= D0 K10"))
        self.assertEqual(rs[1].label, "P0")
        self.assertEqual(rs[1].root.children[0].expr, Contact("X1", "P"))
        self.assertEqual(rs[2].root.children, [Output("END", [], None)])

    def test_missing_output_warns(self):
        (r,) = rungs_of("LD X0\nAND X1")
        self.assertTrue(r.warnings)


class RenderTests(unittest.TestCase):
    def test_text_ladder(self):
        out = render(layout_all(rungs_of("LD X000\nOR Y000\nANI X001\nOUT Y000")))
        self.assertIn("|---| |---+---|/|----", out)
        self.assertIn("( Y000 )", out)
        self.assertIn("|---| |---+", out.splitlines()[4])


if __name__ == "__main__":
    unittest.main()
