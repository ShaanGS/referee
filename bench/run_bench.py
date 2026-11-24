#!/usr/bin/env python3
"""Planted-bug benchmark: what simpler gates miss that Referee catches.

Every controller is run REPS times in two conditions on the QNX target:
  idle   referee check --no-load   (what you see on an idle dev board)
  load   referee check             (background load at priority 20 on every CPU)

From the same measurements, four gates decide pass/fail:
  G1 functional, idle     control law + no crash, from the idle run
  G2 deadline, idle       G1 + deadline + p99 latency, from the idle run
  G3 deadline, under load G1 + deadline + p99 latency, from the loaded run
  Referee                 the full verdict of the loaded run (adds the priority
                          ceiling, monitor-confirmed stalls, hardened target)

The gates are computed from Referee's own checks, so they differ only in which
checks they look at and under which condition, never in how they measure.

Usage: python3 bench/run_bench.py [--reps 3] [--only name,name] [--duration 10]
Writes work/bench-<time>/results.json and results.md.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "bench" / "controllers"
EX = REPO / "examples" / "pid"
CL = REPO / "codelab" / "steps"

# name, kind, source, one-line description. Written before any measurement; the
# predictions are in each source file's header comment.
VARIANTS = [
    ("good",                       "healthy", EX / "good/src/controller.c",          "compute and reply in the receiving thread"),
    ("codelab_step5",              "healthy", CL / "step5_pid.c",                    "the codelab's PID step"),
    ("codelab_step6",              "healthy", CL / "step6_inherit.c",                "the codelab's step that records its own priority"),
    ("healthy_mutex_inherit",      "healthy", BENCH / "healthy_mutex_inherit.c",     "shares a mutex with a priority-10 thread, with priority inheritance"),
    ("healthy_worker_at_plant_prio", "healthy", BENCH / "healthy_worker_at_plant_prio.c", "hands off to a worker created at priority 40"),
    ("bug_worker_low_prio",        "bug",     EX / "bad_worker/src/controller.c",    "hands off to a worker at the default priority 10"),
    ("bug_drops_priority",         "bug",     BENCH / "bug_drops_priority.c",        "lowers its own priority to 10 while handling a message"),
    ("bug_mutex_no_inherit",       "bug",     BENCH / "bug_mutex_no_inherit.c",      "shares a mutex with a priority-10 thread, PTHREAD_PRIO_NONE"),
    ("bug_work_after_reply",       "bug",     BENCH / "bug_work_after_reply.c",      "replies, then does 150 us of bookkeeping in the same thread"),
    ("bug_housekeeping_above_plant", "bug",   BENCH / "bug_housekeeping_above_plant.c", "a priority-50 thread busy 60 us every 10 ms"),
    ("bug_stale_reply",            "bug",     EX / "bad_stale/src/controller.c",     "replies at once with u from a polling loop"),
    ("cheat_stall_hider",          "cheat",   EX / "stall_hider/src/controller.c",   "hides late replies by faking VM stalls with priority-63 hogs"),
    ("cheat_fork_hider",           "cheat",   EX / "fork_hider/src/controller.c",    "the same hogs in a forked child"),
    ("cheat_spawn_hider",          "cheat",   EX / "spawn_hider/src/controller.c",   "forks a fresh hog child per burst"),
]

GATES = {
    "G1": ("idle", ["control law", "no crash"]),
    "G2": ("idle", ["control law", "no crash", "deadline", "p99 latency"]),
    "G3": ("load", ["control law", "no crash", "deadline", "p99 latency"]),
}


def workspace(root: Path, name: str, src: Path) -> Path:
    w = root / name
    (w / "src").mkdir(parents=True)
    shutil.copy(src, w / "src" / "controller.c")
    for h in BENCH.glob("*.h"):
        shutil.copy(h, w / "src" / h.name)
    shutil.copy(EX / "good" / "referee.toml", w / "referee.toml")
    shutil.copy(EX / "good" / "Makefile", w / "Makefile")
    return w


def check(w: Path, idle: bool, duration: float) -> dict:
    cmd = ["sh", "bin/referee", "check", "--project", str(w), "--json", "--duration", str(duration)]
    if idle:
        cmd.append("--no-load")
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
    try:
        r = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"verdict": "ERROR", "error": (p.stderr or p.stdout)[-400:], "checks": []}
    return r


def summarise(r: dict) -> dict:
    checks = {c["name"]: c for c in r.get("checks", [])}
    s = r.get("stats") or {}
    lat = s.get("latency_us") or {}
    return {
        "verdict": r.get("verdict"),
        "failed": [n for n, c in checks.items() if not c["pass"]],
        "pass": {n: c["pass"] for n, c in checks.items()},
        "measured": {n: c["measured"] for n, c in checks.items()},
        "missed": s.get("missed"), "p99_us": lat.get("p99"),
        "stalled_samples": s.get("stalled_samples"),
        "evidence": r.get("evidence"), "run_id": r.get("run_id"),
        "error": r.get("error"),
    }


def gate(run: dict, names: list[str]) -> bool:
    return bool(run["pass"]) and all(run["pass"].get(n, False) for n in names)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--duration", type=float, default=10)
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    root = REPO / "work" / time.strftime("bench-%Y%m%d-%H%M%S")
    root.mkdir(parents=True)
    results = []
    for name, kind, src, desc in VARIANTS:
        if only and name not in only:
            continue
        w = workspace(root, name, src)
        for rep in range(1, a.reps + 1):
            row = {"name": name, "kind": kind, "desc": desc, "rep": rep}
            for cond in ("idle", "load"):
                row[cond] = summarise(check(w, cond == "idle", a.duration))
            for g, (cond, names) in GATES.items():
                row[g] = gate(row[cond], names)
            row["Referee"] = row["load"]["verdict"]
            results.append(row)
            print(f"{name:30} rep {rep}: G1 {'pass' if row['G1'] else 'FAIL'}  G2 {'pass' if row['G2'] else 'FAIL'}"
                  f"  G3 {'pass' if row['G3'] else 'FAIL'}  Referee {row['Referee']}"
                  f"  (load failed: {', '.join(row['load']['failed']) or '-'}; idle failed: {', '.join(row['idle']['failed']) or '-'})",
                  flush=True)
            (root / "results.json").write_text(json.dumps(results, indent=2))
    (root / "results.md").write_text(table(results))
    print("\n" + table(results))
    print(f"results: {root}")
    return 0


def table(results: list[dict]) -> str:
    out = ["| Controller | Kind | G1 functional, idle | G2 deadline, idle | G3 deadline, load | Referee | Referee's failing checks |",
           "|---|---|---|---|---|---|---|"]
    by = {}
    for r in results:
        by.setdefault(r["name"], []).append(r)
    for name, rows in by.items():
        n = len(rows)
        cell = lambda g: f"{sum(r[g] for r in rows)}/{n} pass"
        ver = {}
        for r in rows:
            ver[r["Referee"]] = ver.get(r["Referee"], 0) + 1
        fails = sorted({f for r in rows for f in r["load"]["failed"]})
        out.append(f"| `{name}` | {rows[0]['kind']} | {cell('G1')} | {cell('G2')} | {cell('G3')} | "
                   + ", ".join(f"{k} {v}/{n}" for k, v in sorted(ver.items())) + f" | {', '.join(fails) or '-'} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
