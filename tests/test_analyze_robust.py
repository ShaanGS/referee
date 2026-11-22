"""analyze() must give a FAIL with a reason, never crash or PASS, on missing, partial or
malformed measurements. No target needed."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee.check import DEFAULTS, _merge, analyze  # noqa: E402

SPEC = _merge(DEFAULTS, {})
HEADER = ("seq,period,skipped_before,status,act_interval_cyc,latency_cyc,y,setpoint,u_reply,u_applied,u_ref,"
          "act_cyc,t0_cyc,t1_cyc,probe_arm_cyc\n")


class Robust(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ev = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def verdict_checks(self):
        r = analyze(self.ev, SPEC)
        failed = [c for c in r["checks"] if not c["pass"]]
        self.assertTrue(failed, "expected a failing check")
        return failed[0]["measured"]

    def test_nothing_fetched(self):
        self.assertIn("samples.csv", self.verdict_checks())

    def test_plant_json_garbage(self):
        (self.ev / "samples.csv").write_text(HEADER)
        (self.ev / "plant.json").write_text("{not json")
        self.assertIn("unreadable", self.verdict_checks())

    def test_plant_json_missing_keys(self):
        (self.ev / "samples.csv").write_text(HEADER)
        (self.ev / "plant.json").write_text("{}")
        self.assertIn("cycles_per_sec", self.verdict_checks())

    def test_monitor_files_missing(self):
        (self.ev / "samples.csv").write_text(HEADER)
        (self.ev / "plant.json").write_text(json.dumps(
            {"cycles_per_sec": 1e9, "expected_periods": 10, "periods": 10, "monitor_priority": 120}))
        self.assertIn("monitor.csv", self.verdict_checks())

    def test_truncated_csv_row(self):
        (self.ev / "samples.csv").write_text(HEADER + "0,600,0,0,1000\n")   # judged period, cut off mid-row
        (self.ev / "plant.json").write_text(json.dumps(
            {"cycles_per_sec": 1e9, "expected_periods": 10, "periods": 10}))
        self.assertIn("malformed", self.verdict_checks())


if __name__ == "__main__":
    unittest.main(verbosity=2)
