"""License redaction: verbatim, reversed, base64/base64url/hex at any alignment.
Uses made-up tokens only; never reads a real license."""
import base64
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from referee import check  # noqa: E402

TOK = ["FAKE-SERIAL-7Q2X-91KD", "a1b2c3d4e5f6deadbeef"]
FILE = "[license]\nserial=FAKE-SERIAL-7Q2X-91KD\nkey=a1b2c3d4e5f6deadbeef\n"


class Redact(unittest.TestCase):
    def clean(self, text):
        out = check.redact(text, TOK)
        for t in TOK:
            self.assertNotIn(t, out)
        return out

    def test_verbatim_and_reversed(self):
        out = self.clean("x " + FILE + " " + TOK[0][::-1])
        self.assertIn("[redacted: QNX license]", out)
        self.assertNotIn(TOK[0][::-1], out)

    def test_base64_every_alignment(self):
        for pad in ("", "a", "ab", "abc"):
            for enc in (base64.b64encode, base64.urlsafe_b64encode):
                blob = enc((pad + FILE).encode()).decode()
                out = self.clean(f"leak: {blob} end")
                self.assertNotIn(blob, out, (pad, enc))
                self.assertIn("leak: ", out)

    def test_base64_wrapped_lines(self):
        blob = base64.encodebytes(("zz" + FILE * 3).encode()).decode()   # 76-col lines
        out = check.redact(blob, TOK)
        for line in blob.split():
            if len(line) >= 16:
                self.assertNotIn(line, out)

    def test_hex_any_case_and_odd_offset(self):
        h = FILE.encode().hex()
        for blob in (h, h.upper(), "f" + h, " ".join(h[i:i + 2] for i in range(0, len(h), 2))):
            out = self.clean("dump " + blob)
            self.assertNotIn(blob, out)

    def test_leaves_normal_logs_alone(self):
        log = ("qcc -Vgcc_ntox86_64 -O2 -o build/controller src/controller.c\n"
               "t0_cyc,t1_cyc\n12345678901234567890,12345678901234599999\n"
               "sha256 " + "0" * 64 + "\n" + base64.b64encode(b"just some other data here").decode())
        self.assertEqual(check.redact(log, TOK), log)

    def test_no_tokens_is_identity(self):
        self.assertEqual(check.redact(FILE, []), FILE)

    def test_redact_tree(self):
        orig = check._LICENSE_TOKENS
        check._LICENSE_TOKENS = sorted(TOK, key=len, reverse=True)
        try:
            with tempfile.TemporaryDirectory() as d:
                ev = Path(d)
                (ev / "sub").mkdir()
                (ev / "controller.log").write_text("got " + base64.b64encode(FILE.encode()).decode())
                (ev / "sub" / "build.log").write_text("ok\n")
                (ev / "bin").write_bytes(b"\xff\xfe" + FILE.encode())          # not text: skipped
                self.assertEqual(check.redact_tree(ev), ["controller.log"])
                self.assertNotIn("FAKE", (ev / "controller.log").read_text())
        finally:
            check._LICENSE_TOKENS = orig


if __name__ == "__main__":
    unittest.main()
