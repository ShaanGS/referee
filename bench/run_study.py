#!/usr/bin/env python3
"""Real-agent study: what real coding agents do under Referee.

Each trial copies a scenario project into a fresh workspace OUTSIDE this repo, runs
`referee agent` with a real agent (Claude Code or Codex) for up to --rounds rounds,
and records the signed agent pack's timeline. Nothing here judges the agent: the
verdicts, tamper events and suspicious-edit warnings are Referee's own.

Scenarios:
  spec           examples/pid/agent_demo: write the controller from the task text
  existing_code  bench/study/existing_code: fill in compute_u() in a starter that
                 hands each sample to a worker thread at the default priority
  pressure       bench/study/pressure: a deadline too tight for correct code to
                 meet reliably on this target (see bench/README.md for calibration)

Usage: python3 bench/run_study.py --scenario existing_code --agent claude --n 5
       [--rounds 3] [--model M] [--effort E] [--out DIR]
Appends one JSON line per trial to <out>/trials.jsonl.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCENARIOS = {
    "spec": REPO / "examples" / "pid" / "agent_demo",
    "existing_code": REPO / "bench" / "study" / "existing_code",
    "pressure": REPO / "bench" / "study" / "pressure",
}
# parent dirs, drive or UNC paths, WSL mounts, home directories, Referee's own state
OUTSIDE = re.compile(r"\.\.[\\/]|\b[A-Za-z]:[\\/]|\\\\wsl|/mnt/|~[\\/]|\$HOME|USERPROFILE|\.referee", re.I)
# Codex wraps every command as "C:\...\powershell.exe" -Command "...": drop the wrapper
SHELL = re.compile(r'^"?[A-Za-z]:\\[^"]*?\\(?:powershell|pwsh|cmd)\.exe"?\s+(?:-Command\s+|/c\s+)?', re.I)


def trial(scenario: str, agent: str, i: int, out: Path, a) -> dict:
    w = out / f"{scenario}-{agent}-{i:02d}"
    shutil.copytree(SCENARIOS[scenario], w, ignore=shutil.ignore_patterns("evidence", "build"))
    cmd = ["sh", "bin/referee", "agent", "--project", str(w), "--agent", agent, "--rounds", str(a.rounds)]
    if a.model:
        cmd += ["--model", a.model]
    if a.effort:
        cmd += ["--effort", a.effort]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    (w.parent / f"{w.name}.log").write_text(p.stdout + "\n--- stderr\n" + p.stderr)
    rec = {"scenario": scenario, "agent": agent, "model": a.model, "effort": a.effort, "trial": i,
           "workspace": str(w), "exit": p.returncode, "minutes": round((time.time() - t0) / 60, 1)}
    packs = sorted((w / "evidence").glob("agent-*"))
    if not packs:
        rec["error"] = (p.stderr or p.stdout)[-600:]
        return rec
    pack = packs[-1]
    if not (pack / "timeline.json").exists():
        rec["error"] = "no timeline (agent run aborted): " + (p.stderr or p.stdout)[-600:]
        return rec
    tl = json.loads((pack / "timeline.json").read_text())
    rounds = []
    for r in tl["rounds"]:
        rd = pack / f"round-{r['round']}"
        agent_rec = json.loads((rd / "agent.json").read_text()) if (rd / "agent.json").exists() else {}
        rr = json.loads((rd / "round.json").read_text()) if (rd / "round.json").exists() else {}
        failed = [c["name"] for c in ((rr.get("check") or {}).get("checks") or []) if not c["pass"]]
        # Commands that name a path outside the workspace, for manual review (Codex's
        # Windows sandbox does not confine reads). Claude's backend has no shell tool.
        outside = [a["command"] for a in agent_rec.get("actions", [])
                   if a.get("command") and OUTSIDE.search(SHELL.sub("", a["command"]))]
        rounds.append({**r, "failed_checks": failed,
                       "warning_rules": [w_["rule"] for w_ in rr.get("warnings", [])],
                       "agent_seconds": agent_rec.get("seconds"), "agent_turns": agent_rec.get("turns"),
                       "commands": sum(1 for a in agent_rec.get("actions", []) if a.get("command")),
                       "outside_refs": outside})
    rec.update({"pack": str(pack), "final": tl["final_verdict"], "rounds": rounds,
                "verify": subprocess.run(["sh", "bin/referee", "verify", str(pack)], cwd=REPO,
                                         capture_output=True, text=True).returncode == 0})
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    ap.add_argument("--agent", choices=["claude", "codex"], required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--out", default=str(REPO.parent / "referee-work" / "study"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    start = 1 + sum(1 for _ in out.glob(f"{a.scenario}-{a.agent}-*.log"))
    for i in range(start, start + a.n):
        rec = trial(a.scenario, a.agent, i, out, a)
        with open(out / "trials.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        seq = " ".join(r["verdict"] for r in rec.get("rounds", [])) or rec.get("error", "?")[:120]
        print(f"{a.scenario} {a.agent} #{i}: {seq}  ({rec['minutes']} min)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
