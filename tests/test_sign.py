"""Unit tests for evidence sealing (referee.sign), with a throwaway signing key.

Covers the nested-seal attack: change a sealed round, delete its seal, then let the
outer pack be sealed. Format 2 must catch it; format-1 packs must still verify."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee import sign  # noqa: E402


class Seal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        sign.HOME, sign.KEY, sign.ALLOWED = t / "home", t / "home" / "signing_key", t / "home" / "allowed_signers"
        self.pack = t / "pack"
        self.round = self.pack / "round-1"
        self.round.mkdir(parents=True)
        (self.round / "verdict.json").write_text('{"verdict": "FAIL"}')

    def tearDown(self):
        self.tmp.cleanup()

    def seal_round_then_pack(self, between=None):
        sign.sign_dir(self.round)
        children = {"round-1": sign.manifest_sha(self.round)}
        if between:
            between()
        (self.pack / "timeline.json").write_text("{}")
        sign.sign_dir(self.pack, children)
        return sign.verify_dir(self.pack, sign.ALLOWED)

    def test_untouched_pack_verifies(self):
        ok, problems = self.seal_round_then_pack()
        self.assertTrue(ok, problems)

    def test_round_edited_and_seal_deleted_is_caught(self):
        def attack():
            (self.round / "verdict.json").write_text('{"verdict": "PASS"}')
            (self.round / "manifest.json").unlink()
            (self.round / "manifest.json.sig").unlink()
        ok, problems = self.seal_round_then_pack(attack)
        self.assertFalse(ok)
        self.assertTrue(any("seal missing" in p for p in problems), problems)

    def test_round_edited_after_pack_seal_is_caught(self):
        ok, _ = self.seal_round_then_pack()
        self.assertTrue(ok)
        (self.round / "verdict.json").write_text('{"verdict": "PASS"}')
        ok, problems = sign.verify_dir(self.pack, sign.ALLOWED)
        self.assertFalse(ok)
        self.assertTrue(any("modified" in p for p in problems), problems)

    def test_round_seal_swapped_is_caught(self):
        def attack():   # re-seal the edited round with a key the attacker controls? No key: just copy
            (self.round / "manifest.json").write_text(json.dumps({"format": 2, "files": {}}))
        ok, problems = self.seal_round_then_pack(attack)
        self.assertFalse(ok)
        self.assertTrue(any("seal replaced" in p for p in problems), problems)

    def test_format1_pack_still_verifies(self):
        sign.ensure_key()
        sign.sign_dir(self.round)
        # Rewrite the outer seal the old way: no format key, manifests skipped at any depth.
        (self.pack / "signer.pub").write_text(Path(str(sign.KEY) + ".pub").read_text())
        files = {str(p.relative_to(self.pack)): sign._sha(p) for p in sign._files(self.pack, 1)}
        (self.pack / "manifest.json").write_text(json.dumps({"files": files}))
        import subprocess
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(sign.KEY), "-n", sign.NAMESPACE,
                        str(self.pack / "manifest.json")], check=True, capture_output=True)
        ok, problems = sign.verify_dir(self.pack, sign.ALLOWED)
        self.assertTrue(ok, problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)
