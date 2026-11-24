"""Referee command line."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .check import REPO, CheckError, format_report, run_check
from .target import Target, TargetError


def cmd_check(a) -> int:
    try:
        r = run_check(Path(a.project), duration=a.duration, no_load=a.no_load,
                      log=(lambda *_: None) if a.json else print)
    except (CheckError, TargetError) as e:
        print(f"referee: {e}", file=sys.stderr)
        return 2
    print(json.dumps(r, indent=2, default=str) if a.json else format_report(r))
    return 0 if r["verdict"] == "PASS" else 1


def cmd_agent(a) -> int:
    from .agent import AgentError, ClaudeBackend, CodexBackend, ReplayBackend, format_timeline, run_agent
    if a.agent == "claude":
        backend = ClaudeBackend(max_turns=a.max_turns, model=a.model)
    elif a.agent == "codex":
        backend = CodexBackend(model=a.model, effort=a.effort)
    elif a.agent.startswith("replay:"):
        backend = ReplayBackend(Path(a.agent.split(":", 1)[1]).resolve())
    else:
        print("referee: --agent must be 'claude', 'codex' or 'replay:<dir>'", file=sys.stderr)
        return 2
    project = Path(a.project)
    task = Path(a.task) if a.task else project / "TASK.md"
    try:
        s = run_agent(project, task, a.rounds, backend, duration=a.duration, no_load=a.no_load)
    except (AgentError, CheckError, TargetError) as e:
        print(f"referee: {e}", file=sys.stderr)
        return 2
    print(format_timeline(s))
    return 0 if s["final_verdict"] == "PASS" else 1


def cmd_verify(a) -> int:
    import tempfile
    from .sign import ALLOWED, IDENTITY, NAMESPACE, verify_dir
    allowed = ALLOWED
    if a.signer:                       # someone else's public key, e.g. a published one
        key = Path(a.signer).read_text().split()
        if len(key) < 2 or not key[0].startswith("ssh-"):
            print(f"referee: {a.signer} is not an ssh public key", file=sys.stderr)
            return 2
        tmp = tempfile.NamedTemporaryFile("w", suffix=".allowed", delete=False)
        tmp.write(f'{IDENTITY} namespaces="{NAMESPACE}" {key[0]} {key[1]}\n')
        tmp.close()
        allowed = Path(tmp.name)
    elif not ALLOWED.exists():
        print("referee: no local signing key; pass --signer <public key> to check against a published key",
              file=sys.stderr)
        return 2
    try:
        ok, problems = verify_dir(Path(a.evidence), allowed)
    finally:
        if a.signer:
            allowed.unlink(missing_ok=True)
    if ok:
        print(f"OK: signature valid and all files match ({a.evidence})")
        return 0
    print("INVALID:")
    for p in problems:
        print("  - " + p)
    return 1


def cmd_report(a) -> int:
    from .report import write_report
    d = Path(a.evidence)
    out = Path(a.output) if a.output else None
    if out is None and (d / "manifest.json").exists():
        out = d.parent / f"{d.name}.report.html"   # never add files to a signed folder
    try:
        p = write_report(d, out)
    except FileNotFoundError as e:
        print(f"referee: {e}", file=sys.stderr)
        return 2
    print(p)
    return 0


def cmd_target(a) -> int:
    scripts = REPO / "target"
    if a.action == "up":
        # Foreground: boots the VM and follows its console (on WSL this keeps the VM alive).
        return subprocess.call(["bash", str(scripts / "qemu-console.sh")])
    if a.action == "down":
        return subprocess.call(["bash", str(scripts / "qemu-down.sh")])
    t = Target()
    if t.reachable():
        print(t.ssh("uname -a").stdout.strip())
        return 0
    print("target not reachable")
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="referee", description="Agent-proof verification for QNX real-time code.")
    p.add_argument("--version", action="version", version=f"referee {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="build, run on the QNX target, and judge from Referee's own measurements")
    c.add_argument("--project", default=".", help="project dir containing referee.toml")
    c.add_argument("--duration", type=float, help="override run.duration_s")
    c.add_argument("--no-load", action="store_true", help="skip background load (idle-target run)")
    c.add_argument("--json", action="store_true", help="print the full verdict as JSON")
    c.set_defaults(fn=cmd_check)

    g = sub.add_parser("agent", help="loop an AI agent: edit -> tamper guard -> check -> feedback")
    g.add_argument("--project", default=".")
    g.add_argument("--task", help="task file (default <project>/TASK.md)")
    g.add_argument("--agent", default="claude", help="'claude', 'codex' or 'replay:<dir>'")
    g.add_argument("--rounds", type=int, default=5)
    g.add_argument("--duration", type=float)
    g.add_argument("--no-load", action="store_true")
    g.add_argument("--max-turns", type=int, default=40)
    g.add_argument("--model")
    g.add_argument("--effort", help="reasoning effort for Codex")
    g.set_defaults(fn=cmd_agent)

    rp = sub.add_parser("report", help="(re)write the HTML report for an evidence folder or agent pack")
    rp.add_argument("evidence")
    rp.add_argument("-o", "--output", help="output file (default: inside the folder, or beside it if signed)")
    rp.set_defaults(fn=cmd_report)

    v = sub.add_parser("verify", help="check an evidence directory's signature and file hashes")
    v.add_argument("evidence")
    v.add_argument("--signer", help="verify against this ssh-ed25519 public key instead of your local "
                                    "Referee key (e.g. docs/demo/referee-signer.pub)")
    v.set_defaults(fn=cmd_verify)

    t = sub.add_parser("target", help="start/stop/check the QEMU QNX target")
    t.add_argument("action", choices=["up", "down", "status"])
    t.set_defaults(fn=cmd_target)

    a = p.parse_args(argv)
    return a.fn(a)
