"""The verdict rule: when target noise turns a timing failure into INCONCLUSIVE, and when it
must not. The numbers come from real runs on the QEMU target (bench validation,
2026-11-24). No target needed."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee.check import decide  # noqa: E402

NAMES = ["deadline", "p99 latency", "control law", "no crash", "prio ceiling", "monitor"]
P99 = 120.0


def checks(*failing, referee_side=()):
    return [{"name": n, "pass": n not in failing and n not in referee_side,
             **({"referee_side": True} if n in referee_side else {})} for n in NAMES]


def stats(stall_fraction, stalled, missed=0, p99=4.0, over=0):
    return {"stall_fraction": stall_fraction, "max_stall_fraction": 0.01, "stalled_samples": stalled,
            "missed": missed, "latency_us": {"p99": p99}, "over_p99_limit": over}


class Verdict(unittest.TestCase):
    def test_clean_pass(self):
        self.assertEqual(decide(checks(), stats(0.004, 38), P99), ("PASS", None))

    def test_noisy_run_with_nothing_failing_is_inconclusive(self):
        # a noisy host: 120 stalled samples, 1.2% of the run
        self.assertEqual(decide(checks(), stats(0.012, 120), P99)[0], "INCONCLUSIVE")

    def test_small_deadline_miss_in_a_noisy_run_is_inconclusive(self):
        # 14 missed against 120 stalled samples on a noisy host
        self.assertEqual(decide(checks("deadline"), stats(0.012, 120, missed=14, p99=18.0, over=19), P99)[0],
                         "INCONCLUSIVE")

    def test_thousands_of_misses_are_never_noise(self):
        # bug_drops_priority under load (9210 missed), here on a noisy host
        v, note = decide(checks("deadline", "p99 latency"), stats(0.012, 120, missed=9210, p99=101020.0), P99)
        self.assertEqual(v, "FAIL")
        self.assertIn("rejected", note)

    def test_slow_on_every_reply_is_not_excused_by_probe_excused_samples(self):
        # bug_work_after_reply: p99 187 us (limit 120, under 2x), 0 missed, but every clean
        # reply over the p99 limit. 211 "stalled" samples, 172 of them probe-excused late
        # replies the controller's own slowness produced. Was INCONCLUSIVE before the fix.
        v, note = decide(checks("p99 latency"), stats(0.0222, 211, p99=187.3, over=9289), P99)
        self.assertEqual(v, "FAIL")
        self.assertIn("rejected", note)

    def test_marginal_p99_in_a_noisy_run_stays_inconclusive(self):
        # 3% of replies above the limit, fewer than 3x the stalled samples
        self.assertEqual(decide(checks("p99 latency"), stats(0.012, 120, p99=125.0, over=300), P99)[0],
                         "INCONCLUSIVE")

    def test_p99_far_past_the_limit_is_never_noise(self):
        self.assertEqual(decide(checks("p99 latency"), stats(0.012, 120, p99=250.0, over=100), P99)[0], "FAIL")

    def test_non_timing_failure_is_fail_however_noisy(self):
        # bug_housekeeping_above_plant: prio ceiling, here on a noisy host
        self.assertEqual(decide(checks("prio ceiling"), stats(0.012, 120), P99)[0], "FAIL")

    def test_referee_side_problem_excuses_only_small_timing_failures(self):
        small = decide(checks("deadline", referee_side=("monitor",)), stats(0.001, 10, missed=5), P99)
        big = decide(checks("deadline", referee_side=("monitor",)), stats(0.001, 10, missed=500), P99)
        self.assertEqual((small[0], big[0]), ("INCONCLUSIVE", "FAIL"))

    def test_no_checks_is_fail(self):
        self.assertEqual(decide([], {}, P99)[0], "FAIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
