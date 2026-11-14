import unittest

from ladder import siemens
from ladder.model import Contact, Output, Parallel, Series

# Made-up STEP 7 "Generate Source" output (German mnemonics), one pattern per network.
AWL = """FUNCTION FC 7 : VOID
TITLE =
AUTHOR : TEST
NAME : DEMO
VERSION : 0.1

BEGIN
NETWORK
TITLE =self-holding

      U(    ;
      O     E      0.0;
      O     A      4.0;
      )     ;
      UN    E      0.1;
      =     A      4.0;
NETWORK
TITLE =

      O     M      1.0;
      O     ;
      U     M      2.0;
      UN    M      3.0;
      S     M     10.0;
      R     M     11.0;
NETWORK
TITLE =

      U     M     10.0;
      L     S5T#3S;
      SE    T      5;
      NOP   0;
      NOP   0;
      NOP   0;
      U     T      5;
      =     M     12.0;
NETWORK
TITLE =

      U     E      1.0;
      U(    ;
      U     E      1.5;
      L     W#16#1100;
      SE    T      9;
      NOP   0;
      NOP   0;
      NOP   0;
      U     T      9;
      )     ;
      =     M     13.0;
NETWORK
TITLE =

      U     M     20.0;
      SPBNB _001;
      L     0;
      T     MW    30;
      T     MW    32;
_001: NOP   0;
NETWORK
TITLE =

      U(    ;
      U     M     21.0;
      SPBNB _002;
      L     MD    40;
      L     L#100;
      *D    ;
      T     MD    44;
      UN    OV;
      SAVE  ;
      CLR   ;
_002: U     BIE;
      )     ;
      SPBNB _003;
      L     MD    44;
      T     PAW  256;
_003: NOP   0;
NETWORK
TITLE =

      U     T      5;
      ZV    Z      3;
      BLD   101;
      NOP   0;
      NOP   0;
      U     E      2.0;
      R     Z      3;
      NOP   0;
      LC    Z      3;
      T     MW    50;
      NOP   0;
NETWORK
TITLE =

      U     M     22.0;
      U(    ;
      L     MW    50;
      L     3;
      >=I   ;
      )     ;
      FP    M     23.0;
      =     M     24.0;
NETWORK
TITLE =

      U     E      3.0;
      SPB   M001;
NETWORK
TITLE =

      L     MW    60;
      TAK   ;
      T     MW    62;
NETWORK
TITLE =

M001: UC    FC     8;
      BE    ;
NETWORK
TITLE =

      BE    ;
END_FUNCTION

DATA_BLOCK DB 1
STRUCT
  X : INT ;
END_STRUCT ;
BEGIN
END_DATA_BLOCK
"""

ENGLISH = """ORGANIZATION_BLOCK OB 1
TITLE =
BEGIN
NETWORK
TITLE =

      A     I      0.0;
      AN    I      0.1;
      L     S5T#2S;
      SD    T      1;
      NOP   0;
      NOP   0;
      NOP   0;
      A     T      1;
      =     Q      4.0;
END_ORGANIZATION_BLOCK
"""


def cond_of(rung, k=0):
    return rung.root.children[k].expr


def outs_of(rung, k=0):
    return rung.root.children[k].children


class SiemensTests(unittest.TestCase):
    def setUp(self):
        self.rungs = siemens.convert_text(AWL)

    def net(self, n):
        return [r for r in self.rungs if r.network.split(":")[0] == f"Network {n}"]

    def test_parse(self):
        blocks, lang = siemens.parse_awl(AWL)
        self.assertEqual(lang, "de")
        self.assertEqual([b.name for b in blocks], ["FC7"])  # DB skipped
        self.assertEqual(blocks[0].title, "DEMO")
        self.assertEqual(blocks[0].networks[0].title, "self-holding")
        self.assertEqual(str(blocks[0].networks[0].stmts[2]), "O      A4.0")

    def test_brackets_and_coil(self):
        (r,) = self.net(1)
        self.assertEqual(cond_of(r), Series([Parallel([Contact("E0.0"), Contact("A4.0")]), Contact("E0.1", "NC")]))
        self.assertEqual(outs_of(r), [Output("=", ["A4.0"])])
        self.assertEqual(r.block, "FC7")

    def test_and_before_or_and_shared_rlo(self):
        (r,) = self.net(2)
        self.assertEqual(cond_of(r), Parallel([Contact("M1.0"), Series([Contact("M2.0"), Contact("M3.0", "NC")])]))
        self.assertEqual([o.op for o in outs_of(r)], ["S", "R"])  # one RLO, two outputs

    def test_timer_box_inline(self):
        (r,) = self.net(3)
        c = cond_of(r)
        self.assertEqual(c.items[0], Contact("M10.0"))
        self.assertEqual((c.items[1].kind, c.items[1].text), ("BOX", "S_EVERZ T5 S5T#3S"))
        self.assertEqual(outs_of(r), [Output("=", ["M12.0"])])

    def test_timer_inside_brackets_gets_own_rung(self):
        t, main = self.net(4)
        self.assertEqual(cond_of(t), Contact("E1.5"))
        self.assertEqual(outs_of(t)[0].text, "S_EVERZ T9 W#16#1100 (10s)")
        self.assertEqual(cond_of(main), Series([Contact("E1.0"), Contact("T9")]))  # E1.5 is not here

    def test_move_with_en(self):
        (r,) = self.net(5)
        self.assertEqual(cond_of(r), Contact("M20.0"))
        self.assertEqual(outs_of(r)[0].text, "MOVE 0 → MW30 MW32")

    def test_eno_chain(self):
        (r,) = self.net(6)
        items = cond_of(r).items
        self.assertEqual(items[0], Contact("M21.0"))
        self.assertEqual(items[1].text, "MUL_DI MD40 L#100 → MD44")
        self.assertEqual(outs_of(r)[0].text, "MOVE MD44 → PAW256")

    def test_counter_box(self):
        (r,) = self.net(7)
        box = r.root.children[0]
        self.assertEqual(box.op, "Z_VORW")
        self.assertEqual(box.input_names, ["ZV", "R"])
        self.assertEqual(box.inputs, [Contact("T5"), Contact("E2.0")])
        self.assertIn("DEZ→MW50", box.args)

    def test_compare_and_edge(self):
        (r,) = self.net(8)
        self.assertEqual(cond_of(r), Series([Contact("M22.0"), Contact("", "CMP", ">=I MW50 3"), Contact("M23.0", "P")]))

    def test_jump_to_other_network(self):
        (r,) = self.net(9)
        self.assertEqual(cond_of(r), Contact("E3.0"))
        self.assertEqual(outs_of(r)[0].args, ["M001", "(SPB)"])
        (target,) = self.net(11)
        self.assertEqual(target.label, "M001:")
        self.assertEqual(target.root.children[0].text, "CALL FC8")

    def test_unsupported_network_falls_back_to_stl(self):
        (r,) = self.net(10)
        self.assertEqual(r.root.children[0].op, "STL")
        self.assertIn("TAK", r.warnings[0])

    def test_after_be_padding_ignored(self):
        self.assertEqual(self.net(12), [])
        self.assertEqual(sum(len(r.warnings) for r in self.rungs), 1)  # only the TAK network

    def test_english_mnemonics(self):
        (r,) = siemens.convert_text(ENGLISH)
        c = cond_of(r)
        self.assertEqual(c.items[:2], [Contact("I0.0"), Contact("I0.1", "NC")])
        self.assertEqual(c.items[2].text, "S_ODT T1 S5T#2S")  # English SD = on-delay
        self.assertEqual(r.block, "OB1")


if __name__ == "__main__":
    unittest.main()
