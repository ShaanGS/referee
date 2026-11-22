"""The agent's own config (.claude/**, .mcp.json) is always protected: hooks and MCP
servers run commands, so an agent planting them is TAMPER, and the files are reverted
before the next round starts. Also checks the Claude backend never loads workspace
config (tests/reach_test.py proves the same against a real Claude Code). No target needed."""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee import guard  # noqa: E402
from referee.agent import ClaudeBackend  # noqa: E402


class AgentConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        (self.ws / "referee.toml").write_text("[run]\n")
        (self.ws / ".claude").mkdir()
        (self.ws / ".claude" / "settings.json").write_text("{}")      # the developer's own
        guard.HOME = Path(self.tmp.name) / "state"                     # keep ~/.referee clean
        self.meta = guard.snapshot(self.ws, [])

    def tearDown(self):
        self.tmp.cleanup()

    def test_patterns_always_present(self):
        for p in (".claude/**", ".mcp.json", "referee.toml"):
            self.assertIn(p, self.meta["patterns"])

    def test_planted_mcp_server_reverted(self):
        (self.ws / ".mcp.json").write_text(json.dumps({"mcpServers": {"x": {"command": "sh"}}}))
        ev = guard.enforce(self.ws, self.meta)
        self.assertEqual([(e["path"], e["change"]) for e in ev], [(".mcp.json", "added")])
        self.assertFalse((self.ws / ".mcp.json").exists())

    def test_hook_added_to_existing_settings_reverted(self):
        (self.ws / ".claude" / "settings.json").write_text('{"hooks": {"Stop": []}}')
        ev = guard.enforce(self.ws, self.meta)
        self.assertEqual([(e["path"], e["change"]) for e in ev], [(".claude/settings.json", "modified")])
        self.assertEqual((self.ws / ".claude" / "settings.json").read_text(), "{}")

    def test_new_local_settings_reverted(self):
        (self.ws / ".claude" / "settings.local.json").write_text('{"hooks": {}}')
        ev = guard.enforce(self.ws, self.meta)
        self.assertEqual([e["path"] for e in ev], [".claude/settings.local.json"])
        self.assertFalse((self.ws / ".claude" / "settings.local.json").exists())

    def test_claude_backend_ignores_workspace_config(self):
        # Pin the base command so the test doesn't need Claude Code installed (CI doesn't have it).
        with mock.patch.dict(os.environ, {"REFEREE_CLAUDE_CMD": "claude"}):
            cmd = ClaudeBackend()._cmd(Path("/mnt/d/x"))
        self.assertEqual(cmd[0], "claude")
        i = cmd.index("--setting-sources")
        self.assertEqual(cmd[i + 1], "user")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertNotIn("--mcp-config", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
