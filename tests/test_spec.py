"""Spec validation: values the plant can't run are rejected up front, by name. No target needed."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee.check import CheckError, load_spec_bytes  # noqa: E402


class Spec(unittest.TestCase):
    def test_defaults_load(self):
        self.assertEqual(load_spec_bytes(b"")["realtime_loop"]["deadline_us"], 200)

    def test_deadline_that_swamps_the_probe_is_rejected(self):
        # measured 2026-11-24: at 20 us a correct controller failed with 5,900+ misses because
        # the reply probe (every 5 us at priority 120) starved the plant's CPU
        for dl in (b"10", b"20", b"49"):
            with self.subTest(dl=dl), self.assertRaises(CheckError) as e:
                load_spec_bytes(b"[realtime_loop]\ndeadline_us = " + dl + b"\n")
            self.assertIn("deadline_us", str(e.exception))

    def test_lowest_deadline_is_accepted(self):
        self.assertEqual(load_spec_bytes(b"[realtime_loop]\ndeadline_us = 50\n")["realtime_loop"]["deadline_us"], 50)

    def test_bad_rate_is_rejected(self):
        for rate in (b"0", b"50000"):
            with self.subTest(rate=rate), self.assertRaises(CheckError):
                load_spec_bytes(b"[realtime_loop]\nrate_hz = " + rate + b"\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
