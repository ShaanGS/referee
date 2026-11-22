"""A controller that blocks the plant for a long time must be charged for every period it
blocked, even when a short VM pause (seen by the monitor) lands inside the block, and
even when the plant never got to send again before the run ended.

Modelled on a real run (codelab step 7 starved under load): 199 samples in 10 s, a 5 s
reply, and the old analysis said "0 missed of 9500". No target needed."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee.check import DEFAULTS, _merge, analyze  # noqa: E402

SPEC = _merge(DEFAULTS, {})
CPS = 1_000_000_000            # 1 GHz: 1 cycle = 1 ns
P = 1_000_000                  # 1 ms period in cycles
HEADER = ("seq,period,skipped_before,status,act_interval_cyc,latency_cyc,y,setpoint,u_reply,u_applied,u_ref,"
          "act_cyc,t0_cyc,t1_cyc,probe_arm_cyc\n")


def build(ev: Path, sends, expected=2000, gaps=()):
    """sends: list of (period, latency_cyc). Writes samples/plant/monitor/watch files."""
    rows, prev_act, prev_period = [], None, -1
    for seq, (period, lat) in enumerate(sends):
        act = period * P + 1_000
        t0 = act + 2_000
        rows.append(f"{seq},{period},{period - prev_period - 1},0,{0 if prev_act is None else act - prev_act},"
                    f"{lat},0,1,1,1,1,{act},{t0},{t0 + lat},0")
        prev_act, prev_period = act, period
    (ev / "samples.csv").write_text(HEADER + "\n".join(rows) + "\n")
    ticks = [k * P + 500 for k in range(expected)]
    for g0, g1 in gaps:                           # remove monitor ticks inside each pause
        ticks = [t for t in ticks if not (g0 < t < g1)]
    mon = [(a, b) for a, b in zip(ticks, ticks[1:]) if b - a > P]
    (ev / "monitor.csv").write_text("gap_start,gap_end\n" + "".join(f"{a},{b}\n" for a, b in mon))
    (ev / "ctl_watch.csv").write_text("t_cyc,tid,prio,state\n")
    (ev / "plant.json").write_text(json.dumps({
        "cycles_per_sec": CPS, "expected_periods": expected, "periods": expected,
        "samples": len(sends), "monitor_priority": 120, "ctl_watch": True, "probe": False}))


def deadline(ev):
    r = analyze(ev, SPEC)
    return next(c for c in r["checks"] if c["name"] == "deadline"), r["stats"]


class BlockedPlant(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ev = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_good_run_is_clean(self):
        build(self.ev, [(k, 3_000) for k in range(2000)])
        c, st = deadline(self.ev)
        self.assertTrue(c["pass"], c)
        self.assertEqual(st["missed"], 0)

    def test_block_with_pause_inside_is_charged(self):
        # 300 ms reply at period 1200 with a 3 ms VM pause inside it; the plant then
        # skips periods 1201..1500.
        sends = [(k, 3_000) for k in range(1200)] + [(1200, 300 * P)] + [(k, 3_000) for k in range(1501, 2000)]
        g0 = 1200 * P + 50 * P
        build(self.ev, sends, gaps=[(g0, g0 + 3 * P)])
        c, st = deadline(self.ev)
        self.assertFalse(c["pass"], c)
        self.assertGreaterEqual(st["missed"], 290, st)        # 300 skipped, minus at most the pause

    def test_plant_blocked_until_the_end_is_charged(self):
        # last send at period 1600 never gets a reply in time: periods 1601..1999 never ran.
        sends = [(k, 3_000) for k in range(1600)] + [(1600, 5_000 * P)]
        build(self.ev, sends)
        c, st = deadline(self.ev)
        self.assertFalse(c["pass"], c)
        self.assertGreaterEqual(st["missed"], 399, st)

    def test_real_pause_while_plant_idle_still_excused(self):
        # Honest controller; a 4 ms VM pause hits while the plant is between periods.
        g0 = 1000 * P + 100_000
        sends = [(k, 3_000) for k in range(1000)] + [(k, 3_000) for k in range(1005, 2000)]
        build(self.ev, sends, gaps=[(g0, g0 + 4 * P)])
        c, st = deadline(self.ev)
        self.assertEqual(st["missed"], 0, (c, st))
        self.assertGreater(st["stalled_samples"], 0, st)


if __name__ == "__main__":
    unittest.main(verbosity=2)
