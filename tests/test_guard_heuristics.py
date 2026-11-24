"""guard.heuristics: the 'priority above the plant' hint. Lines below are taken from the
2026-11-24 agent study (bench/study). No target needed."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee.guard import heuristics  # noqa: E402

ABOVE = "requests priority above plant (40)"


def above(line: str) -> bool:
    w = heuristics({"src/controller.c": ""}, {"src/controller.c": line + "\n"}, [], plant_priority=40)
    return any(x["rule"] == ABOVE for x in w)


class AbovePlant(unittest.TestCase):
    def test_caught(self):
        for line in ("#define CTL_PRIORITY 50",         # caught before the fix too
                     "#define CONTROL_PRIO 60",          # missed before the fix
                     "#define CTRL_PRIO 60",             # missed before the fix
                     "    for (prio = (max < 60 ? max : 60); prio > 10; prio--) {",  # missed before
                     "static const int kPriorities[] = { 50, 45, 40, 30, 25, 21 };"):
            with self.subTest(line=line):
                self.assertTrue(above(line))

    def test_not_above(self):
        for line in ("#define CTL_PRIORITY 39", "sp.sched_priority = 40;", "int prio = 21;"):
            with self.subTest(line=line):
                self.assertFalse(above(line))

    def test_comments_ignored(self):
        self.assertFalse(above(" *   see the message. Raising our priority (round 2 used 60) does not help, since"))
        self.assertFalse(above("// was priority 60"))

    def test_known_miss_is_still_missed(self):
        # documented in guard.py and docs/limits.md: the runtime ceiling check is the verdict
        self.assertFalse(above("    static const int wanted[] = { 60, 55, 50, 45, 41, 40, 35, 30, 25, 21 };"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
