"""Unit tests for referee.agent._restore_evidence: whatever the agent does to evidence/
during its turn is undone and reported."""
import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee.agent import _restore_evidence, _snapshot_tree  # noqa: E402


class EvidenceGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.pack = self.project / "evidence" / "agent-1"
        (self.pack / "round-1" / "check").mkdir(parents=True)
        (self.pack / "round-1" / "check" / "verdict.json").write_text('{"verdict": "FAIL"}')
        (self.pack / "round-2").mkdir()
        (self.pack / "round-2" / "prompt.md").write_text("task")
        self.snap = _snapshot_tree(self.pack)

    def tearDown(self):
        self.tmp.cleanup()

    def restore(self):
        return {(c["change"], c["path"]) for c in _restore_evidence(self.project, self.pack, self.snap)}

    def test_no_change_no_tamper(self):
        self.assertEqual(self.restore(), set())

    def test_planted_check_folder_removed(self):
        planted = self.pack / "round-2" / "check"
        planted.mkdir()
        (planted / "samples.csv").write_text("fake")
        changes = self.restore()
        self.assertFalse(planted.exists())
        self.assertIn(("added", "evidence/agent-1/round-2/check/samples.csv"), changes)

    def test_modified_verdict_restored(self):
        v = self.pack / "round-1" / "check" / "verdict.json"
        v.write_text('{"verdict": "PASS"}')
        self.assertIn(("modified", "evidence/agent-1/round-1/check/verdict.json"), self.restore())
        self.assertEqual(v.read_text(), '{"verdict": "FAIL"}')

    def test_deleted_file_restored(self):
        (self.pack / "round-2" / "prompt.md").unlink()
        self.assertIn(("deleted", "evidence/agent-1/round-2/prompt.md"), self.restore())
        self.assertEqual((self.pack / "round-2" / "prompt.md").read_text(), "task")

    def test_symlink_planted_removed(self):
        target = Path(self.tmp.name) / "outside.txt"
        target.write_text("x")
        os.symlink(target, self.pack / "round-2" / "check")
        changes = self.restore()
        self.assertFalse((self.pack / "round-2" / "check").exists())
        self.assertIn(("added", "evidence/agent-1/round-2/check"), changes)
        self.assertTrue(target.exists())              # never follows the link

    def test_pack_replaced_by_symlink(self):
        import shutil
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        shutil.rmtree(self.pack)
        os.symlink(elsewhere, self.pack)
        changes = self.restore()
        self.assertFalse(self.pack.is_symlink())
        self.assertEqual((self.pack / "round-1" / "check" / "verdict.json").read_text(), '{"verdict": "FAIL"}')
        self.assertIn(("replaced", "evidence/agent-1"), changes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
