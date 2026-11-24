"""Codex command, JSONL results, and tamper protection. No Codex install or target needed."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from referee import guard  # noqa: E402
from referee.agent import AgentError, CodexBackend  # noqa: E402


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"REFEREE_CODEX_CMD": "codex"}))
        self.project = Path("/mnt/d/project with spaces")

    def test_command(self):
        required = ["codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
                    "--ignore-user-config", "--ignore-rules", "--color", "never", "--sandbox",
                    "workspace-write", "-c", 'approval_policy="never"', "-c",
                    "sandbox_workspace_write.network_access=false", "-C", str(self.project)]
        for model, effort, extra in [
            (None, None, []),
            ("example", None, ["-m", "example"]),
            (None, "high", ["-c", 'model_reasoning_effort="high"']),
            ("example", "high", ["-m", "example", "-c", 'model_reasoning_effort="high"']),
        ]:
            with self.subTest(model=model, effort=effort):
                cmd = CodexBackend(model, effort)._cmd(self.project)
                self.assertEqual(cmd, required + extra + ["-"])
                for flag in ("--trust", "--dangerously-bypass-approvals-and-sandbox",
                             "--dangerously-bypass-hook-trust", "--add-dir"):
                    self.assertNotIn(flag, cmd)

    def test_windows_install_from_wsl(self):
        exe = "/mnt/c/Users/u/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe"
        which = {"codex": None, "codex.exe": exe}.get
        conv = subprocess.CompletedProcess([], 0, "D:\\project with spaces\r\n", "")
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("referee.agent.shutil.which", side_effect=which), \
                mock.patch("referee.agent.platform.release", return_value="6.6.87-microsoft-standard-WSL2"), \
                mock.patch("referee.agent.subprocess.run", return_value=conv) as run:
            cmd = CodexBackend()._cmd(self.project)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["wslpath", "-w", str(self.project)])
            self.assertEqual(cmd[0], exe)
            self.assertEqual(cmd[cmd.index("-C") + 1], "D:\\project with spaces")
            self.assertIn('windows.sandbox="unelevated"', cmd)
            self.assertEqual(cmd[-1], "-")
            with self.assertRaises(AgentError):
                CodexBackend()._cmd(Path("/home/u/project"))

    def run_jsonl(self, stdout, returncode=0, stderr=""):
        backend = CodexBackend()
        result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with mock.patch("referee.agent.subprocess.run", return_value=result) as run:
            record = backend.run(self.project, "edit the project", 1)
        run.assert_called_once_with(backend._cmd(self.project), input="edit the project", cwd=self.project,
                                    capture_output=True, text=True, timeout=1800)
        return record

    def test_sample_turn(self):
        thread = "01a20dd1-f27b-7cb1-bac6-a2b778b7add9"
        usage = {"input_tokens": 23628, "cached_input_tokens": 13824, "cache_write_input_tokens": 0,
                 "output_tokens": 5, "reasoning_output_tokens": 0}
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": thread}),
            '{"type":"turn.started"}',
            "non-JSON status line",
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"ok"}}',
            json.dumps({"type": "turn.completed", "usage": usage}),
        ])
        with mock.patch("referee.agent.time.monotonic", side_effect=[10, 11.26]):
            self.assertEqual(self.run_jsonl(stdout),
                             {"backend": "codex", "summary": "ok", "turns": 0, "cost_usd": None,
                              "session_id": thread, "tokens": usage, "actions": [], "seconds": 1.3})

    def test_last_message_and_usage(self):
        stdout = """{"type":"item.completed","item":{"type":"agent_message","text":"first"}}
{"type":"item.completed","item":{"type":"command_execution","command":"cat ../secret"}}
{"type":"turn.completed","usage":{"output_tokens":3}}
{"type":"item.completed","item":{"type":"file_change","changes":[{"path":"src/a.c","kind":"update"}]}}
{"type":"item.completed"}
{"type":"item.completed","item":{"type":"agent_message","text":"last"}}
{"type":"turn.completed","usage":{"output_tokens":7}}"""
        record = self.run_jsonl(stdout)
        self.assertEqual(record["summary"], "last")
        self.assertEqual(record["turns"], 3)
        self.assertEqual(record["tokens"], {"output_tokens": 7})
        self.assertEqual(record["actions"], [{"type": "command_execution", "command": "cat ../secret"},
                                             {"type": "file_change", "paths": ["src/a.c"]},
                                             {"type": None}])

    def test_completion_without_message_or_usage(self):
        record = self.run_jsonl('{"type":"turn.completed"}')
        self.assertEqual(record["summary"], "")
        self.assertIsNone(record["tokens"])

    def test_failed_runs(self):
        completed = '{"type":"turn.completed"}'
        for stdout, code in [
            ('{"type":"turn.failed"}\n' + completed, 0),
            (completed + '\n{"type":"error"}', 0),
            ('{"type":"turn.started"}', 0),
            (completed, 1),
        ]:
            with self.subTest(stdout=stdout, code=code):
                with self.assertRaises(AgentError) as error:
                    self.run_jsonl(stdout, code, "x" * 400 + "truncated")
                self.assertIn("x" * 400, str(error.exception))
                self.assertNotIn("truncated", str(error.exception))


class CodexConfigGuard(unittest.TestCase):
    def test_config_and_instructions_reverted(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(guard, "HOME", Path(tmp) / "state"):
            ws = Path(tmp) / "ws"
            ws.mkdir()
            instructions = ws / "AGENTS.md"
            instructions.write_text("Original instructions\n")
            meta = guard.snapshot(ws, [])
            config = ws / ".codex" / "config.toml"
            config.parent.mkdir()
            config.write_text('model = "example"\n')
            instructions.write_text("Planted instructions\n")

            events = guard.enforce(ws, meta)
            self.assertCountEqual([(e["path"], e["change"]) for e in events],
                                  [(".codex/config.toml", "added"), ("AGENTS.md", "modified")])
            self.assertFalse(config.exists())
            self.assertEqual(instructions.read_text(), "Original instructions\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
