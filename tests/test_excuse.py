"""Unit tests for the late-reply excuse (referee.check._excuse_late_replies).

Synthetic probe data, no target needed. Cycles are microseconds here (cps = 1e6)."""
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee.check import _excuse_late_replies  # noqa: E402

CPS = 1e6                # 1 cycle = 1 us
PLANT = {"probe": True, "probe_first_ns": 100_000, "probe_every_ns": 50_000}
DEADLINE = 200


def row(arm, t0, t1, status=0):
    return {"probe_arm_cyc": str(arm), "t0_cyc": str(t0), "t1_cyc": str(t1), "status": str(status)}


def run(rows, wake, plant=PLANT):
    with tempfile.TemporaryDirectory() as d:
        ev = Path(d)
        (ev / "probe.csv").write_text("arrive_cyc,done_cyc\n" + "".join(f"{a},{b}\n" for a, b in wake))
        return _excuse_late_replies(ev, plant, rows, set(), DEADLINE, CPS)


class Excuse(unittest.TestCase):
    def test_host_pause_is_excused(self):
        # Controller needs 3 us, but the vCPU paused from t=1005 to t=1255: reply at 1258.
        # Probe due at 1100 (arm 1000 + 100) only ran at 1255: blocked 1105..1255.
        rows = [row(1000, 1001, 1258)]
        self.assertEqual(run(rows, [(1255, 1256)]), {0})

    def test_slow_controller_is_not_excused(self):
        # Controller computes for 257 us; probe wake-ups arrive on time (1100, 1150, 1200, 1250).
        rows = [row(1000, 1001, 1258)]
        wake = [(1100, 1101), (1150, 1151), (1200, 1201), (1250, 1251)]
        self.assertEqual(run(rows, wake), set())

    def test_slow_controller_with_small_pause_is_not_excused(self):
        # 240 us of its own work plus a 30 us pause: still over the deadline without the pause.
        rows = [row(1000, 1001, 1271)]
        wake = [(1100, 1101), (1180, 1181), (1200, 1201), (1250, 1251)]
        self.assertEqual(run(rows, wake), set())

    def test_on_time_reply_untouched(self):
        self.assertEqual(run([row(1000, 1001, 1004)], []), set())

    def test_probe_off_never_excuses(self):
        rows = [row(1000, 1001, 1258)]
        self.assertEqual(run(rows, [(1255, 1256)], plant={"probe": False}), set())

    def test_unarmed_sample_never_excused(self):
        rows = [row(0, 1001, 1258)]
        self.assertEqual(run(rows, [(1255, 1256)]), set())

    def test_errors_never_excused(self):
        rows = [row(1000, 1001, 1258, status=260)]
        self.assertEqual(run(rows, [(1255, 1256)]), set())

    def test_no_wakeup_before_reply_counts_to_reply(self):
        # Pause covers the whole rest of the window; the queued probe ran after the reply.
        rows = [row(1000, 1001, 1290)]
        self.assertEqual(run(rows, [(1291, 1292)]), {0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
