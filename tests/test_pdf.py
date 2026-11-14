import os
import unittest

from ladder.pdf_reader import parse_lines, read_pdf

HERE = os.path.dirname(__file__)


class PdfLineTests(unittest.TestCase):
    def test_headers_comments_and_continuations(self):
        lines = ["MELSOFT GX Developer   MAIN   Page 1",
                 "   0  LD   X000     START PB",
                 "   1  MOV  K100",
                 "           D0",
                 "   6  OUT  Y000"]
        ins, skipped = parse_lines(lines)
        self.assertEqual([(i.step, i.op, i.args) for i in ins],
                         [(0, "LD", ["X000"]), (1, "MOV", ["K100", "D0"]), (6, "OUT", ["Y000"])])
        self.assertEqual(ins[0].comment, "START PB")
        self.assertEqual(len(skipped), 1)

    def test_two_columns_are_put_in_step_order(self):
        ins, _ = parse_lines(["  0 LD X0        50 LD X5",
                              "  1 OUT Y0       51 OUT Y5"])
        self.assertEqual([i.step for i in ins], [0, 1, 50, 51])


class PdfFileTests(unittest.TestCase):
    def test_printed_pdf(self):
        ins = read_pdf(os.path.join(HERE, "fixtures", "gx_print.pdf"))
        self.assertEqual(len(ins), 16)
        self.assertEqual(str(ins[6 - 0]), "AND X003")
        self.assertEqual(ins[7].args, ["T0", "K50"])


if __name__ == "__main__":
    unittest.main()
