"""`referee check`: build -> deploy -> run probes on QNX -> verdict + evidence."""
from __future__ import annotations

import base64
import binascii
import bisect
import csv
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from pathlib import Path

from .sign import sign_dir
from .target import Target, TargetError

REPO = Path(__file__).resolve().parent.parent
PROBES = REPO / "probes"
INCLUDE = PROBES / "include"
SDP = Path(os.environ.get("QNX_SDP", Path.home() / "qnx800"))
CACHE = Path.home() / ".referee"

DEFAULTS = {
    "target": {"host": "127.0.0.1", "port": 2222, "user": "qnxuser", "controller_user": "user1",
               "keep_awake": True,
               # Stall monitor: started with `sudo -n` so it can sit above 63, the ceiling
               # for unprivileged QNX threads. "none" falls back to the heuristic.
               "monitor": "sudo", "monitor_priority": 120, "plant_cpu": 0},
    "build": {"command": "make", "artifact": "build/controller"},
    "run": {"duration_s": 10, "warmup_s": 0.5},
    "realtime_loop": {"name": "referee/controller", "rate_hz": 1000, "deadline_us": 200,
                      "max_missed": 0, "p99_latency_us": 120, "plant_priority": 40,
                      "load": {"enabled": True, "threads": 0, "priority": 20, "duty": 0.9}},
    "model": {"kind": "pid", "kp": 2.0, "ki": 40.0, "kd": 0.001, "plant_gain": 1.0,
              "plant_tau_s": 0.05, "u_min": -10.0, "u_max": 10.0, "tolerance": 1e-3},
    "no_crash": {"enabled": True},
    "protected": {"paths": ["referee.toml", "tests/**"]},
}


TIMING_CHECKS = {"deadline", "p99 latency"}


class CheckError(RuntimeError):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_spec(project: Path, spec_path: Path | None = None) -> dict:
    f = spec_path or project / "referee.toml"
    if not f.exists():
        raise CheckError(f"spec not found: {f}")
    with f.open("rb") as fh:
        return _merge(DEFAULTS, tomllib.load(fh))


def load_spec_bytes(data: bytes) -> dict:
    return _merge(DEFAULTS, tomllib.loads(data.decode()))


# The build is the one place where content the agent wrote (Makefile, build scripts)
# runs on the host. It runs on a throwaway COPY of the project (so it can't touch the
# real workspace or its evidence), in a user+mount+net+pid namespace: no network; home,
# /mnt (Windows drives, the Referee repo, other projects), /tmp and /run replaced by
# empty tmpfs; then only the copy (rw), Referee's protocol headers and the QNX SDP
# (read-only) mounted back. So a build step can't reach ~/.referee (signing key,
# baselines), ~/.ssh, credentials, local sockets or the Referee code, and can't phone
# home. Only the artifact comes out.
_SANDBOX_SH = r'''
set -e
WS="$1"; INC="$2"; SDPD="$3"; CMD="$4"; H="$5"
# Staging on a private tmpfs over /var/tmp (inside this mount namespace only), so the
# paths we need survive hiding /tmp, /run, /mnt and $HOME, wherever they live (the repo
# may be cloned under /tmp or $HOME).
[ -d /var/tmp ] || { echo "sandbox: /var/tmp missing" >&2; exit 97; }
mount -t tmpfs -o mode=700 tmpfs /var/tmp
T=/var/tmp/sbx
mkdir -p "$T/ws" "$T/inc" "$T/sdp" "$T/lic"
mount --bind "$WS" "$T/ws"
mount --bind "$INC" "$T/inc"
mount --bind "$SDPD" "$T/sdp"
# qcc needs the QNX license and writes a lock file beside it: give it a private copy.
HAVE_LIC=0
if [ -d "$H/.qnx/license" ]; then cp -a "$H/.qnx/license/." "$T/lic/"; HAVE_LIC=1; fi
[ -f "$H/.qnx/qnx_env.ini" ] && cp "$H/.qnx/qnx_env.ini" "$T/env.ini"
# Hide: Windows drives / other projects / the Referee repo, home (keys, ~/.referee),
# host temp files and local sockets.
mount -t tmpfs -o mode=755 tmpfs /mnt
mount -t tmpfs -o mode=755 tmpfs "$H"
mount -t tmpfs -o mode=1777 tmpfs /tmp
[ -d /run ] && mount -t tmpfs -o mode=755 tmpfs /run
# Put back only what the build needs, at its original path.
mkdir -p "$WS" "$INC" "$SDPD"
mount --bind "$T/ws" "$WS"
mount --bind "$T/inc" "$INC";  mount -o remount,bind,ro "$INC"
mount --bind "$T/sdp" "$SDPD"; mount -o remount,bind,ro "$SDPD"
mkdir -p "$H/.qnx/license"
if [ "$HAVE_LIC" = 1 ]; then mount --bind "$T/lic" "$H/.qnx/license"; fi
[ -f "$T/env.ini" ] && cp "$T/env.ini" "$H/.qnx/qnx_env.ini"
# Hide the staging area itself (it holds writable views of the SDP and headers).
mount -t tmpfs -o mode=1777 tmpfs /var/tmp
cd "$WS"
source "$SDPD/qnxsdp-env.sh" >/dev/null
exec bash -c "$CMD"
'''


def sandboxed_build(cmd: str, project: Path) -> tuple[subprocess.CompletedProcess, bool]:
    """Run the build command in `project` (Referee passes a throwaway copy); returns
    (process, sandboxed?)."""
    env_sh = SDP / "qnxsdp-env.sh"
    if not env_sh.exists():
        raise CheckError(f"QNX SDP not found at {SDP} (set QNX_SDP)")
    home = str(Path.home())
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "LANG": "C.UTF-8",
           "REFEREE_INCLUDE": str(INCLUDE)}            # scrubbed: no tokens/keys from our env
    if os.environ.get("REFEREE_BUILD_SANDBOX", "1") != "0":
        argv = ["unshare", "--user", "--map-root-user", "--mount", "--net", "--pid", "--fork",
                "--mount-proc", "bash", "-c", _SANDBOX_SH, "sandbox",
                str(project), str(INCLUDE), str(SDP), cmd, home]
        return subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True, timeout=600), True
    script = f"source {shlex.quote(str(env_sh))} >/dev/null && {cmd}"
    return subprocess.run(["bash", "-c", script], cwd=project, env=env, capture_output=True,
                          text=True, timeout=600), False


def qnx_shell(cmd: str, cwd: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env_sh = SDP / "qnxsdp-env.sh"
    if not env_sh.exists():
        raise CheckError(f"QNX SDP not found at {SDP} (set QNX_SDP)")
    script = f"source {shlex.quote(str(env_sh))} >/dev/null && {cmd}"
    return subprocess.run(["bash", "-c", script], cwd=cwd, env={**os.environ, **(extra_env or {})},
                          capture_output=True, text=True)


def build_probes() -> Path:
    """Compile Referee's own probes (cached by source hash). Never from the workspace."""
    srcs = [PROBES / "plant.c", PROBES / "load.c", INCLUDE / "referee" / "protocol.h"]
    h = hashlib.sha256(b"".join(s.read_bytes() for s in srcs)).hexdigest()[:12]
    out = CACHE / "probes" / h
    if (out / "referee_plant").exists() and (out / "referee_load").exists():
        return out
    out.mkdir(parents=True, exist_ok=True)
    q = lambda p: shlex.quote(str(p))
    cmd = (f"qcc -Vgcc_ntox86_64 -O2 -Wall -I{q(INCLUDE)} -o {q(out / 'referee_plant')} {q(PROBES / 'plant.c')} -lm && "
           f"qcc -Vgcc_ntox86_64 -O2 -Wall -o {q(out / 'referee_load')} {q(PROBES / 'load.c')}")
    p = qnx_shell(cmd, REPO)
    if p.returncode != 0:
        raise CheckError("building Referee probes failed:\n" + p.stderr)
    return out


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return math.nan
    i = max(0, min(len(sorted_vals) - 1, math.ceil(p / 100 * len(sorted_vals)) - 1))
    return sorted_vals[i]


def _excuse_late_replies(ev: Path, plant: dict, rows: list, skip: set, deadline_us: float, cps: float) -> set:
    """Late replies the target itself caused, proven by the reply probe.

    The plant armed a high-resolution timer for the priority-120 monitor just before each
    MsgSend (at `probe_arm_cyc`): first wake-up `probe_first_ns` later, then every
    `probe_every_ns`, until the reply. For a wake-up due at d that the monitor only got at
    a, the CPU was unavailable even at priority 120 during [d, a]: nothing the controller
    can do (it can't go above 63) delays that thread. The monitor's own handling time
    [a, done] is Referee's, not the controller's, so it is removed too. A reply is excused
    only if, after removing all of that, it still met the deadline. A controller that is
    slow on its own is never excused: the probe's wake-ups arrive on time, so nothing is
    removed. Due times are computed from a timestamp taken just before arming, so they are
    never later than the real ones; a 5 us allowance covers the arming call and the
    timer's 1 us tolerance.
    """
    probe = ev / "probe.csv"
    if not plant.get("probe") or not probe.exists() or "probe_arm_cyc" not in (rows[0] if rows else {}):
        return set()
    wake = sorted(tuple(int(v) for v in line.split(",")) for line in probe.read_text().split()[1:])
    arrive = [a for a, _ in wake]
    first = plant["probe_first_ns"] * cps / 1e9
    every = plant["probe_every_ns"] * cps / 1e9
    slack = 5e-6 * cps
    dl = deadline_us * cps / 1e6
    excused = set()
    for i, r in enumerate(rows):
        arm, t0, t1 = int(r["probe_arm_cyc"]), int(r["t0_cyc"]), int(r["t1_cyc"])
        if i in skip or int(r["status"]) != 0 or not arm or t1 - t0 <= dl:
            continue
        spans = []
        d = arm + first
        while d < t1:
            j = bisect.bisect_left(arrive, d)
            got = arrive[j] if j < len(arrive) else t1
            spans.append((d + slack, min(got, t1)))
            d += every
        lo, hi = bisect.bisect_left(arrive, t0), bisect.bisect_right(arrive, t1)
        spans += [(a, min(done, t1)) for a, done in wake[lo:hi]]
        blocked, end = 0.0, t0
        for s, e in sorted(spans):           # length of the union, clipped to [t0, t1]
            s = max(s, end)
            if e > s:
                blocked += e - s
                end = e
        if (t1 - t0) - blocked <= dl:
            excused.add(i)
    return excused


def analyze(ev: Path, spec: dict) -> dict:
    """Judge one run's evidence folder. Never raises on bad measurements: a malformed file
    (truncated CSV, wrong types) is a FAIL with the reason, so every run gets a verdict."""
    try:
        return _analyze(ev, spec)
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError) as e:
        return {"checks": [{"name": "run", "pass": False,
                            "measured": f"measurements malformed ({type(e).__name__}: {e})",
                            "limit": "complete measurements"}],
                "stats": {}, "plant": {}, "load": {}, "run": {}}


def _analyze(ev: Path, spec: dict) -> dict:
    rt, model, run = spec["realtime_loop"], spec["model"], spec["run"]

    def _json(name: str) -> dict:
        try:
            v = json.loads((ev / name).read_text() or "{}") if (ev / name).exists() else {}
            return v if isinstance(v, dict) else {"error": f"{name} is not an object"}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"error": f"{name} unreadable"}
    plant, runinfo = _json("plant.json"), _json("run.json")
    load = {}
    if (ev / "load.log").exists() and (ev / "load.log").read_text().strip():
        try:
            load = json.loads((ev / "load.log").read_text().strip().splitlines()[-1])
        except json.JSONDecodeError:
            load = {}

    checks = []
    # No proof, no pass: missing, partial or malformed measurements are a FAIL, never a
    # crash and never a silent fallback to weaker rules. (The code under test could try to
    # starve Referee's probes, e.g. by filling the target's /tmp.)
    need = ["cycles_per_sec", "expected_periods", "periods"]
    missing = [] if (ev / "samples.csv").exists() else ["samples.csv"]
    missing += [f"plant.json:{k}" for k in need if k not in plant]
    if plant.get("monitor_priority", 0) > 0:
        missing += [f for f in ("monitor.csv", "ctl_watch.csv") if not (ev / f).exists()]
    if "error" in plant or missing:
        reason = plant.get("error") or ("measurements incomplete: missing " + ", ".join(missing))
        checks.append({"name": "run", "pass": False, "measured": str(reason),
                       "limit": "controller reachable at " + rt["name"] + ", complete measurements"})
        return {"checks": checks, "stats": {}, "plant": plant, "load": load, "run": runinfo}

    cps = float(plant["cycles_per_sec"])
    rate = float(rt["rate_hz"])
    period_us = 1e6 / rate
    warm = int(float(run["warmup_s"]) * rate)
    with (ev / "samples.csv").open() as fh:
        all_rows = list(csv.DictReader(fh))

    # Target stalls: the target (VM/host) paused, so samples in and right after the pause
    # say nothing about the controller; they are reported and excluded, never silently
    # dropped. Free time = time the plant was not waiting for the controller; if it
    # exceeds a period + stall_us, the plant was held up.
    stall_thr = period_us + float(rt.get("stall_us", 500))
    thr_cyc = stall_thr * cps / 1e6
    free = [0.0] + [(int(all_rows[i]["act_interval_cyc"]) - int(all_rows[i - 1]["latency_cyc"])) * 1e6 / cps
                    for i in range(1, len(all_rows))]
    stalled_idx, preempted_idx, other_held_idx, late_excused_idx = set(), set(), set(), set()
    monitor = ev / "monitor.csv"
    attribution = "heuristic"
    mon_gaps = []
    if monitor.exists() and plant.get("monitor_priority", 0) > 0 and "t1_cyc" in (all_rows[0] if all_rows else {}):
        # A held-up plant is only excused if Referee's monitor (a priority the code under
        # test cannot reach, same CPU as the plant) saw the same gap. Otherwise the target
        # was running and something above the plant's priority took the CPU.
        attribution = "monitor"
        rec_gaps = sorted(tuple(int(v) for v in line.split(",")) for line in monitor.read_text().split()[1:])
        mon_gaps = [(a, b) for a, b in rec_gaps if b - a > thr_cyc]
        t1 = [int(r["t1_cyc"]) for r in all_rows]
        for g0, g1 in mon_gaps:
            lo, hi = bisect.bisect_right(t1, g0), bisect.bisect_left(t1, g1)
            for i in range(max(1, lo), min(hi, len(all_rows) - 1) + 1):
                stalled_idx.update(range(i, min(i + 3, len(all_rows))))
        held = [i for i in range(1, len(all_rows)) if free[i] > stall_thr and i not in stalled_idx]
        # Who held it up? The monitor read every controller thread's state and priority
        # on every tick; a controller thread RUNNING/READY above the plant inside the
        # window convicts the controller. Otherwise it was other system work: excused
        # like a stall (and counted against the same noise budget).
        watch = ev / "ctl_watch.csv"
        if plant.get("ctl_watch") and watch.exists():
            viol = sorted(int(line.split(",")[0]) for line in watch.read_text().split()[1:])
            for i in held:
                w0, w1 = int(all_rows[i - 1]["t1_cyc"]), int(all_rows[i]["act_cyc"])
                if bisect.bisect_right(viol, w1) > bisect.bisect_left(viol, w0):
                    preempted_idx.add(i)
                else:
                    other_held_idx.add(i)
                    stalled_idx.update(range(i, min(i + 3, len(all_rows))))
        else:
            preempted_idx = set(held)      # can't see the controller: blame it (fail safe)
        late_excused_idx |= _excuse_late_replies(ev, plant, all_rows, stalled_idx, rt["deadline_us"], cps)
        stalled_idx |= late_excused_idx
    else:
        for i in range(1, len(all_rows)):
            if free[i] > stall_thr:
                stalled_idx.update(range(i, min(i + 3, len(all_rows))))
    judged = lambda i: int(all_rows[i]["period"]) >= warm
    rows = [r for i, r in enumerate(all_rows) if judged(i) and i not in stalled_idx]
    stalled = sum(1 for i in stalled_idx if judged(i))
    preempted = sorted(int(all_rows[i]["seq"]) for i in preempted_idx if judged(i))

    ok = [r for r in rows if int(r["status"]) == 0]
    lat = sorted(int(r["latency_cyc"]) * 1e6 / cps for r in ok)
    late = sum(1 for v in lat if v > rt["deadline_us"])
    errors = len(rows) - len(ok)
    # Periods the plant skipped before a sample, counting only those after the warm-up.
    jskip = lambda r: max(0, min(int(r["skipped_before"]), int(r["period"]) - warm))
    skipped = sum(jskip(r) for r in rows)
    expected = int(plant["expected_periods"]) - warm
    # Periods the plant never got to: it stopped early, or it sat blocked in its last
    # MsgSend (waiting on the controller) until the run ended.
    last_period = max((int(r["period"]) for r in all_rows), default=-1)
    missing_tail = max(0, int(plant["expected_periods"]) - int(plant["periods"]),
                       int(plant["expected_periods"]) - 1 - max(last_period, warm - 1))
    # An excused (stalled) sample excuses only what the monitor's pauses explain. Its own
    # reply still counts as late if it is late with the pauses removed, and periods the
    # plant skipped because the PREVIOUS reply was slow are charged with that reply's
    # pause-free latency. Without this, a short VM pause inside a multi-second block
    # would excuse the whole block as one "stalled sample". The monitor only records pauses
    # longer than the stall threshold, so up to that much unexplained time per excused
    # sample is still given the benefit of the doubt (bounded by the 0.5% stall budget).
    charged_late = charged_skipped = 0
    if attribution == "monitor":
        g_start = [a for a, _ in mon_gaps]

        def paused(a: int, b: int) -> int:
            tot = 0
            for g0, g1 in mon_gaps[max(0, bisect.bisect_left(g_start, a) - 1):]:
                if g0 >= b:
                    break
                tot += max(0, min(b, g1) - max(a, g0))
            return tot

        dl_cyc = rt["deadline_us"] * cps / 1e6
        p_cyc = cps / rate
        for i in sorted(stalled_idx):
            if not judged(i):
                continue
            r = all_rows[i]
            t0, t1 = int(r["t0_cyc"]), int(r["t1_cyc"])
            if (i not in late_excused_idx and int(r["status"]) == 0
                    and t1 - t0 - paused(t0, t1) > dl_cyc + thr_cyc):
                charged_late += 1
            if i > 0 and jskip(r) > 0:
                q = all_rows[i - 1]
                q0, q1 = int(q["t0_cyc"]), int(q["t1_cyc"])
                charged_skipped += min(jskip(r), max(0, int((q1 - q0 - paused(q0, q1) - thr_cyc) // p_cyc)))
    missed = min(expected, late + errors + skipped + missing_tail + len(preempted) + charged_late + charged_skipped)
    stall_frac = stalled / expected if expected else 0.0
    max_stall = float(rt.get("max_stall_fraction", 0.005))

    diffs = [abs(float(r["u_reply"]) - float(r["u_ref"])) for r in ok]
    bad_model = sum(1 for d in diffs if not d <= model["tolerance"])
    rms = math.sqrt(sum((float(r["setpoint"]) - float(r["y"])) ** 2 for r in rows) / len(rows)) if rows else math.nan

    stats = {"judged_periods": expected, "replies": len(ok), "late": late, "skipped": skipped,
             "errors": errors, "missing_tail": missing_tail, "missed": missed,
             "charged_in_excused": charged_late + charged_skipped,
             "latency_us": {"p50": _pct(lat, 50), "p99": _pct(lat, 99), "p999": _pct(lat, 99.9),
                            "max": lat[-1] if lat else math.nan},
             "model_max_abs_err": max(diffs) if diffs else math.nan, "model_bad": bad_model,
             "tracking_rms": rms, "stalled_samples": stalled, "stall_fraction": stall_frac,
             "max_stall_fraction": max_stall, "stall_attribution": attribution,
             "monitor_gaps": len(mon_gaps), "monitor_ticks": plant.get("monitor_ticks"),
             "preempted": len(preempted), "preempted_seqs": preempted[:50],
             "stalled_seqs": sorted(int(all_rows[i]["seq"]) for i in stalled_idx if judged(i))[:2000],
             "held_by_other": sum(1 for i in other_held_idx if judged(i)),
             "late_excused": sum(1 for i in late_excused_idx if judged(i)),
             "probe": plant.get("probe"), "probe_wakeups": plant.get("probe_wakeups"),
             "ctl_watch": bool(plant.get("ctl_watch")), "ctl_samples": plant.get("ctl_samples"),
             "ctl_violations": plant.get("ctl_violations"),
             "ctl_max_active_priority": plant.get("ctl_max_active_priority"),
             "ctl_threads_max": plant.get("ctl_threads_max"),
             "ctl_offcpu_ticks": plant.get("ctl_offcpu_ticks")}

    checks.append({"name": "deadline", "pass": missed <= rt["max_missed"],
                   "measured": f"{missed} missed of {expected} (late {late}, skipped {skipped}, errors {errors}"
                               + (f", plant held up {len(preempted)}" if preempted else "")
                               + (f", never sent {missing_tail}" if missing_tail else "")
                               + (f", unexplained by stalls {charged_late + charged_skipped}" if charged_late + charged_skipped else "") + ")",
                   "limit": f"<= {rt['max_missed']} over {rt['deadline_us']} us"})
    p99 = stats["latency_us"]["p99"]
    checks.append({"name": "p99 latency", "pass": bool(lat) and p99 <= rt["p99_latency_us"],
                   "measured": f"{p99:.1f} us" if lat else "no judged replies", "limit": f"<= {rt['p99_latency_us']} us"})
    checks.append({"name": "control law", "pass": bool(diffs) and bad_model == 0,
                   "measured": (f"{bad_model} of {len(diffs)} replies off, max err {stats['model_max_abs_err']:.3g}"
                                if diffs else "no judged replies"),
                   "limit": f"<= {model['tolerance']:g}"})
    if spec["no_crash"]["enabled"]:
        alive = bool(runinfo.get("controller_alive")) and not plant.get("controller_gone")
        checks.append({"name": "no crash", "pass": alive,
                       "measured": "controller alive at end" if alive else "controller died",
                       "limit": "alive"})
    # Priority ceiling: a controller above the plant's priority could delay Referee's
    # own timer (and fake "target stalls"). Inheriting the plant's priority is fine.
    ceiling = rt["plant_priority"]
    n = runinfo.get("controller_priority_samples", 0)
    if attribution == "monitor" and plant.get("ctl_watch"):
        # Every controller thread, every tick, read by the prio-120 monitor. Blocked
        # threads can't delay anyone, so only RUNNING/READY ones count.
        nv, ns = int(plant.get("ctl_violations") or 0), plant.get("ctl_samples")
        mx = plant.get("ctl_max_active_priority")
        over = int(plant.get("ctl_watch_overflow") or 0)
        procs = plant.get("ctl_procs_max")
        new_procs = int(plant.get("ctl_new_procs") or 0)
        ok = nv == 0 and not preempted and not over and not new_procs
        measured = (f"{nv} of {ns} ticks had a thread of the controller's user running above the plant (max {mx})" if nv
                    else f"never above the plant in {ns} ticks ({'highest controller thread seen running: ' + str(mx) if mx is not None and mx >= 0 else 'no controller thread was running when sampled'})")
        if procs and procs > 1:
            measured += f"; watched all {procs} processes of the controller's user"
        if new_procs:
            measured += (f"; {new_procs} new process(es) of the controller's user appeared during the run,"
                         " though process creation was locked")
        if over:
            cap = plant.get("ctl_threads_cap", 64)
            measured += (f"; over the watch limit ({cap} threads / 32 processes) on {over} ticks:"
                         " what Referee can't see counts as a violation")
        if plant.get("ctl_spawn_locked"):
            measured += "; fork/spawn locked for the run"
        elif "ctl_spawn_locked" in plant:
            measured += "; WARNING: could not lock fork/spawn"
        if preempted:
            measured += f"; held the plant up {len(preempted)}x"
    elif attribution == "monitor":
        maxp = runinfo.get("controller_max_active_priority")
        ok = not preempted and (maxp is None or maxp <= ceiling)
        measured = (f"plant held up {len(preempted)}x by higher-priority work" if preempted
                    else "plant never held up while target ran")
        measured += f"; running prio max {maxp if maxp is not None else '-'} ({n} samples)"
    else:
        maxp = runinfo.get("controller_max_priority")
        ok = maxp is not None and maxp <= ceiling
        measured = f"max {maxp} over {n} samples (sampled only: no stall monitor)" if maxp is not None else "not sampled"
    checks.append({"name": "prio ceiling", "pass": ok, "measured": measured, "limit": f"<= {ceiling} (plant)"})
    if plant.get("monitor_priority", 0) > 0:
        exp_ticks = int(plant["expected_periods"])
        ticks_ok = ((plant.get("monitor_ticks") or 0) >= 0.95 * exp_ticks
                    and not plant.get("monitor_gaps_dropped") and not plant.get("probe_dropped"))
        probe = "" if plant.get("probe") is None else (
            "; reply probe on" if plant["probe"] else f"; reply probe off ({plant.get('probe_error')})")
        if plant.get("monitor_gaps_dropped"):
            probe += f"; {plant['monitor_gaps_dropped']} monitor gaps dropped (buffer full)"
        if plant.get("probe_dropped"):
            probe += f"; {plant['probe_dropped']} probe wake-ups dropped (buffer full)"
        checks.append({"name": "monitor", "pass": ticks_ok, "referee_side": True,
                       "measured": f"{plant.get('monitor_ticks')} ticks at prio {plant['monitor_priority']}{probe}",
                       "limit": f">= 95% of {exp_ticks}"})
    return {"checks": checks, "stats": stats, "plant": plant, "load": load, "run": runinfo}


def _remote_script(spec: dict, remote: str, duration: float, with_load: bool, monitor: bool = False) -> str:
    rt, m, ld, T = spec["realtime_loop"], spec["model"], spec["realtime_loop"]["load"], spec["target"]
    keep_awake = bool(T.get("keep_awake"))
    plant = (f"./referee_plant -n {shlex.quote(rt['name'])} -f {rt['rate_hz']} -s {duration} "
             f"-P {rt['plant_priority']} -T 100 -g {m['kp']},{m['ki']},{m['kd']} "
             f"-m {m['plant_gain']},{m['plant_tau_s']},{m['u_min']},{m['u_max']} -o samples.csv")
    if monitor:
        plant = (f"sudo -n {plant} -M {int(T['monitor_priority'])} -c {int(T['plant_cpu'])}"
                 f" -D {float(rt['deadline_us']):g}")
    # The controller is started separately, as a different QNX user (see run_check).
    lines = [
        f"cd {remote} || exit 1",
        'LPID=""',
        'KPID=""',
    ]
    if keep_awake:
        # Priority 1 = just above idle: never delays real work, keeps vCPUs from halting.
        lines += [f"./referee_load -t 0 -p 1 -d 1.0 -s {duration + 2} > keepawake.log 2>&1 &", "KPID=$!"]
    if with_load:
        lines += [f"./referee_load -t {ld['threads']} -p {ld['priority']} -d {ld['duty']} -s {duration + 2} > load.log 2>&1 &",
                  "LPID=$!"]
    lines += [
        f"{plant} > plant.json 2> plant.err",
        "PRC=$?",
        '[ -n "$LPID" ] && wait $LPID',
        '[ -n "$KPID" ] && wait $KPID',
        'echo "{\\"plant_rc\\": $PRC}" > run.json',
    ]
    return "\n".join(lines) + "\n"


# Target hardening, run as the probe user (it has sudo) before every run. Two ways a
# controller could otherwise become root and reach past every check (kill the plant, edit
# measurements, run hogs under another user): qconn, the IDE's process launcher, runs as
# root and launches anything for anyone who connects to port 8000 (it comes back on
# reboot, so it is stopped every run); and ssh password logins as the probe user, who has
# sudo (switched off for good: Referee only uses keys). The original sshd_config is kept
# as sshd_config.referee-orig.
_HARDEN_SH = r'''
# SLM supervises the launcher and restarts it when killed: ask SLM to stop it, then kill
# strays. Processes are matched by NAME (pidin -f an), never by argument text: this
# script's own text shows up in pidin's argument column.
for n in /dev/slm-init /dev/slm; do
  [ -e $n ] && [ -x /proc/boot/slmctl ] && sudo -n /proc/boot/slmctl -n $n "stop qconn" >/dev/null 2>&1
done
sleep 0.2
for p in $(sudo -n pidin -f an | awk '$2 ~ /(^|\/)qconn$/ {print $1}' | sort -u); do sudo -n kill -9 "$p"; done
sleep 0.5
if sudo -n pidin -f an | awk '$2 ~ /(^|\/)qconn$/' | grep -q . || sudo -n netstat -an | grep -q '\.8000 .*LISTEN'
then echo "qconn=running"; else echo "qconn=stopped"; fi
C=/etc/ssh/sshd_config
if sudo -n /usr/bin/sshd -T -f $C 2>/dev/null | grep -qi '^passwordauthentication yes'; then
  sudo -n sh -c "[ -f $C.referee-orig ] || cp -p $C $C.referee-orig
    { echo '# Referee: keys only, so code under test cannot log in as another user'
      echo 'PasswordAuthentication no'
      grep -vi '^[[:space:]]*PasswordAuthentication' $C.referee-orig; } > $C.new &&
    /usr/bin/sshd -t -f $C.new && mv $C.new $C" &&
  for p in $(sudo -n pidin -f aA | awk '$2=="/usr/bin/sshd" {print $1}'); do sudo -n kill -HUP "$p"; done &&
  echo "sshd=changed"
fi
'''


def harden_target(tgt: Target) -> dict:
    """Stop qconn, switch ssh password logins off; return what the target looks like after.
    'hardened' is judged from outside: no qconn process, and the running sshd (asked over
    the wire, no password sent) offers no password login."""
    out = tgt.ssh(_HARDEN_SH, timeout=60, check=False).stdout
    qconn = "stopped" if "qconn=stopped" in out else "running"
    methods = set()
    for _ in range(10):                      # sshd re-execs on SIGHUP: give it a moment
        methods = tgt.auth_methods()
        if methods and "password" not in methods and "keyboard-interactive" not in methods:
            break
        time.sleep(0.5)
    pw = bool(methods & {"password", "keyboard-interactive"}) or not methods
    return {"qconn": qconn, "ssh_methods": sorted(methods), "sshd_changed": "sshd=changed" in out,
            "hardened": qconn == "stopped" and not pw}


_PRIO_RE = re.compile(r"^(\d+)[a-z]$")


def _sample_priorities(ctl: Target, cpid: str, stop: threading.Event, out: list) -> None:
    """Every second, record (priority, state) of every thread of the controller."""
    while not stop.wait(1.0):
        try:
            p = ctl.ssh(f"pidin -p {cpid}", timeout=10, check=False)
        except Exception:
            continue
        for line in p.stdout.splitlines()[1:]:
            toks = line.split()
            for j, tok in enumerate(toks):
                m = _PRIO_RE.match(tok)
                if m:
                    out.append((int(m.group(1)), toks[j + 1] if j + 1 < len(toks) else "?"))
                    break


def _fresh_dir(ev: Path) -> None:
    """Create the evidence folder new. Refuse one that already exists or a path that runs
    through a symlink: everything Referee analyses must be something it wrote itself."""
    ev = Path(os.path.abspath(ev))
    ev.parent.mkdir(parents=True, exist_ok=True)
    if os.path.realpath(ev.parent) != str(ev.parent):
        raise CheckError(f"evidence path goes through a symlink, refusing: {ev.parent}")
    try:
        ev.mkdir()
    except FileExistsError:
        raise CheckError(f"evidence folder already exists, refusing to reuse it: {ev}") from None


_LICENSE_TOKENS: list[str] | None = None


_REDACTED = "[redacted: QNX license]"
_B64_RUN = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}(?:\r?\n[A-Za-z0-9+/_-]{4,}={0,2})*")  # wrapped lines too
_HEX_RUN = re.compile(r"(?:[0-9a-fA-F]{2}[\s:]?){8,}")


def _license_tokens() -> list[str]:
    global _LICENSE_TOKENS
    if _LICENSE_TOKENS is None:
        toks = set()
        for f in (Path.home() / ".qnx" / "license").glob("*"):
            try:
                toks |= {t for t in re.split(r"[\s=:,;\"']+", f.read_text(errors="ignore")) if len(t) >= 8}
            except OSError:
                pass
        _LICENSE_TOKENS = sorted(toks, key=len, reverse=True)
    return _LICENSE_TOKENS


def _decodings(blob: str):
    """Plausible byte decodings of a base64/base64url/hex run, at every alignment."""
    s = re.sub(r"[\s:]", "", blob).rstrip("=")
    if re.fullmatch(r"[0-9a-fA-F]+", s):
        for off in (0, 1):
            h = s[off:]
            try:
                yield bytes.fromhex(h[: len(h) // 2 * 2])
            except ValueError:
                pass
    s = s.replace("=", "")
    for off in range(4):
        b = s[off:].replace("-", "+").replace("_", "/")
        b = b[: len(b) // 4 * 4] if len(b) % 4 == 1 else b + "=" * (-len(b) % 4)
        if b:
            try:
                yield base64.b64decode(b, validate=True)
            except (ValueError, binascii.Error):
                pass


def redact(text: str, tokens: list[str] | None = None) -> str:
    """Remove QNX license strings from anything the agent may read: verbatim, reversed,
    and inside base64/base64url/hex runs (each run is decoded at every alignment and
    dropped whole if a license token is in it). Still best effort: the build needs the
    license, so a hostile build could encode it in a way not covered here (XOR, a custom
    alphabet, numbers in samples.csv). README "Known limits" says so."""
    toks = _license_tokens() if tokens is None else sorted(tokens, key=len, reverse=True)
    if not toks:
        return text
    for t in toks:
        text = text.replace(t, _REDACTED).replace(t[::-1], _REDACTED)
    needles = [t.encode() for t in toks]

    def scrub(m: re.Match) -> str:
        for raw in _decodings(m.group(0)):
            if any(n in raw or n[::-1] in raw for n in needles):
                return _REDACTED
        return m.group(0)

    text = _B64_RUN.sub(scrub, text)
    return _HEX_RUN.sub(scrub, text)


def redact_tree(ev: Path) -> list[str]:
    """Redact every text file in an evidence folder in place (before it is signed).
    Returns the files that changed."""
    if not _license_tokens():
        return []
    changed = []
    for f in sorted(ev.rglob("*")):
        if not f.is_file() or f.is_symlink() or f.stat().st_size > 64 * 1024 * 1024:
            continue
        try:
            text = f.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        new = redact(text)
        if new != text:
            f.write_text(new)
            changed.append(str(f.relative_to(ev)))
    return changed


def _build_in_copy(project: Path, spec: dict, run_id: str, log) -> tuple[subprocess.CompletedProcess, bool, bytes | None]:
    """Build a throwaway copy of the project; return (process, sandboxed, artifact bytes).
    The copy omits evidence/ and any artifact already in the tree (Referee never trusts a
    prebuilt binary), and is deleted afterwards, so the build can't leave anything behind
    in the real workspace."""
    root = CACHE / "build"
    root.mkdir(parents=True, exist_ok=True)
    bdir = Path(tempfile.mkdtemp(prefix=f"{run_id}-", dir=root))
    try:
        ws = bdir / "ws"
        shutil.copytree(project, ws, symlinks=True,
                        ignore=lambda d, names: ["evidence"] if Path(d) == project else [])
        artifact = ws / spec["build"]["artifact"]
        if not os.path.realpath(artifact).startswith(str(ws) + os.sep):
            raise CheckError(f"build artifact path leaves the project: {spec['build']['artifact']}")
        if artifact.is_symlink() or artifact.exists():
            artifact.unlink()
        # The sandbox stage must live outside $HOME: the sandbox hides $HOME under a tmpfs.
        b, sandboxed = sandboxed_build(spec["build"]["command"], ws)
        ok = artifact.is_file() and not artifact.is_symlink()
        return b, sandboxed, artifact.read_bytes() if ok else None
    finally:
        shutil.rmtree(bdir, ignore_errors=True)


def run_check(project: Path, duration: float | None = None, no_load: bool = False, log=print,
              spec_path: Path | None = None, ev: Path | None = None, sign: bool = True,
              spec: dict | None = None) -> dict:
    """spec: judge with this (trusted, in-memory) spec instead of the workspace copy.
    spec_path: same, from a file. ev: evidence directory (default <project>/evidence/<run_id>)."""
    project = project.resolve()
    trusted = spec is not None or spec_path is not None
    spec = spec if spec is not None else load_spec(project, spec_path)
    duration = float(duration or spec["run"]["duration_s"])
    with_load = spec["realtime_loop"]["load"]["enabled"] and not no_load
    run_id = time.strftime("%Y%m%d-%H%M%S")
    if ev is None:
        base, n = run_id, 2
        while (project / "evidence" / run_id).exists() or (project / "evidence" / run_id).is_symlink():
            run_id, n = f"{base}-{n}", n + 1
        ev = project / "evidence" / run_id
    _fresh_dir(ev)
    t_start = time.time()

    result = {"run_id": run_id, "project": str(project), "duration_s": duration, "with_load": with_load,
              "keep_awake": bool(spec["target"].get("keep_awake")),
              "spec_source": "trusted baseline" if trusted else "workspace referee.toml"}
    (ev / "spec.json").write_text(json.dumps(spec, indent=2))

    def finish(analysis: dict) -> dict:
        checks = analysis["checks"]
        result.update(analysis)
        st = analysis.get("stats") or {}
        noisy = st.get("stall_fraction", 0) > st.get("max_stall_fraction", 1)
        failed = [c for c in checks if not c["pass"] and not c.get("referee_side")]
        referee_broken = any(not c["pass"] and c.get("referee_side") for c in checks)
        timing_only = all(c["name"] in TIMING_CHECKS for c in failed)
        if (noisy or referee_broken) and timing_only and failed:
            # Noise, or a Referee-side problem, only excuses failures it could plausibly
            # have caused: not thousands of misses from a few dozen stalls, not a p99 far
            # past the limit. (A blatantly slow controller can itself overflow the monitor's
            # probe log, so "Referee broke" is never a free pass either.)
            lim = spec["realtime_loop"]["p99_latency_us"]
            p99 = (st.get("latency_us") or {}).get("p99", 0)
            if st.get("missed", 0) > max(10, 3 * st.get("stalled_samples", 0)) or p99 > 2 * lim:
                noisy = referee_broken = False
                result["noise_excuse"] = "rejected: failures far larger than the target noise"
        if not checks:
            result["verdict"] = "FAIL"
        elif failed and not ((noisy or referee_broken) and timing_only):
            result["verdict"] = "FAIL"
        elif noisy or referee_broken:
            # Too much of the run was a stalled target: timing can't be attributed to the
            # controller either way. Non-timing failures above still give FAIL.
            result["verdict"] = "INCONCLUSIVE"
        else:
            result["verdict"] = "PASS"
        if result["verdict"] == "PASS" and not (result.get("target") or {}).get("hardened"):
            # A target where the code under test could have become root proves nothing.
            result["verdict"] = "INCONCLUSIVE"
            result["inconclusive_reason"] = "target not hardened (qconn running or ssh password logins on)"
        result["elapsed_s"] = round(time.time() - t_start, 1)
        scrubbed = redact_tree(ev)             # the agent reads evidence: no license in it
        if scrubbed:
            result["redacted_files"] = scrubbed
            log(f"[referee] removed QNX license strings from: {', '.join(scrubbed)}")
        (ev / "verdict.json").write_text(json.dumps(result, indent=2, default=str))
        result["evidence"] = str(ev)
        try:
            from .report import write_report
            write_report(ev)                      # before signing, so the page is in the manifest
        except Exception as e:                    # a broken page must never change a verdict
            log(f"[referee] report not written: {e}")
        if sign:
            sign_dir(ev)
            result["signed"] = True
        return result

    log(f"[referee] building probes")
    probes = build_probes()
    log(f"[referee] building project (in a throwaway copy): {spec['build']['command']}")
    b, sandboxed, art = _build_in_copy(project, spec, run_id, log)
    result["build_sandbox"] = sandboxed
    if not sandboxed:
        log("[referee] WARNING: build sandbox disabled (REFEREE_BUILD_SANDBOX=0); build steps run unconfined")
    (ev / "build.log").write_text(redact(b.stdout + b.stderr))
    result["artifact_sha256"] = hashlib.sha256(art).hexdigest() if art is not None else None
    if b.returncode != 0 or art is None:
        return finish({"checks": [{"name": "build", "pass": False,
                                   "measured": f"exit {b.returncode}" + ("" if art is not None else ", no artifact"),
                                   "limit": "builds"}]})

    T = spec["target"]
    tgt = Target(T["host"], T["port"], T["user"])                       # Referee's probes
    ctl = Target(T["host"], T["port"], T.get("controller_user", "user1"))  # code under test
    for t in (tgt, ctl):
        if not t.reachable():
            raise CheckError(f"target {t.dest}:{t.port} not reachable; start it with `referee target up`"
                             " (and install the key for that user with target/install_key.py)")

    # Probes live in a directory only Referee's user can read; the controller runs as
    # another user, so it can't kill the plant or touch its measurements.
    remote = f"/tmp/referee/{run_id}"
    cdir = f"/tmp/referee-ctl-{run_id}"
    log(f"[referee] deploying probes to {tgt.dest}:{remote}, controller to {ctl.dest}:{cdir}")
    tgt.ssh(f"mkdir -p {remote} && chmod 700 {remote}")
    tgt.put(probes / "referee_plant", f"{remote}/referee_plant")
    tgt.put(probes / "referee_load", f"{remote}/referee_load")
    monitor = T.get("monitor") == "sudo" and tgt.ssh("sudo -n true", check=False).returncode == 0
    if T.get("monitor") == "sudo" and not monitor:
        log("[referee] WARNING: `sudo -n` not available for the probe user; no stall monitor, "
            "stalls are attributed heuristically (weaker)")
    result["stall_monitor"] = monitor
    if monitor:
        result["target"] = harden_target(tgt)
        if not result["target"]["hardened"]:
            log(f"[referee] WARNING: target not hardened ({result['target']}): no PASS possible")
    else:
        result["target"] = {"hardened": False, "reason": "no sudo for the probe user"}
    script = _remote_script(spec, remote, duration, with_load, monitor)
    (ev / "run.sh").write_text(script)
    tgt.put(ev / "run.sh", f"{remote}/run.sh")
    tgt.ssh(f"chmod 700 {remote}/referee_plant {remote}/referee_load")
    # Nothing of the controller's user may survive from an earlier run (a forked child could
    # otherwise sit there and hold up this run's plant). As that user, kill -9 -1 kills all
    # of its processes and only those (verified on the QNX 8 target); it also drops this
    # ssh session, hence check=False.
    ctl.ssh("kill -9 -1", check=False)
    ctl.ssh(f"mkdir -p {cdir}")
    with tempfile.NamedTemporaryFile(prefix="referee-ctl-", delete=False) as fh:
        fh.write(art)
    try:
        ctl.put(Path(fh.name), f"{cdir}/controller")
    finally:
        os.unlink(fh.name)
    ctl.ssh(f"chmod +x {cdir}/controller")

    # `cd; nohup ... &` (not `cd && nohup ... &`): backgrounding the && list would keep
    # the ssh session's stdout open and hang.
    # With the stall monitor, pin the controller (runmask and inherit mask, so its threads
    # too) to the plant's CPU: then a host pause that delays the controller usually shows up
    # as a monitor gap too (one shorter than a 1 ms tick can still slip between ticks). A controller that moves itself elsewhere only loses that excuse:
    # delays the monitor can't see always count against it.
    pin = f"on -C {int(T['plant_cpu'])} " if monitor else ""
    result["controller_cpu"] = int(T["plant_cpu"]) if monitor else None
    cpid = ctl.ssh(f"cd {cdir} || exit 1; nohup {pin}./controller > controller.log 2>&1 < /dev/null & echo $!",
                   timeout=30).stdout.strip()
    if not cpid.isdigit():
        raise CheckError(f"could not start controller on target: {cpid!r}")
    time.sleep(0.5)

    log(f"[referee] running {duration:g} s at {spec['realtime_loop']['rate_hz']} Hz"
        + (f" with background load" if with_load else " (no load)"))
    prios: list[int] = []
    stop = threading.Event()
    sampler = threading.Thread(target=_sample_priorities, args=(ctl, cpid, stop, prios), daemon=True)
    # Without the monitor, a once-a-second priority sample over ssh is all there is. With
    # it, the monitor watches every tick, and the sampler's ssh logins as the controller's
    # user would only add noise and processes of that user.
    if not monitor and not os.environ.get("REFEREE_NO_PRIO_SAMPLING"):
        sampler.start()
    try:
        p = tgt.ssh(f"sh {remote}/run.sh", timeout=duration + 90, check=False)
        (ev / "target.log").write_text(p.stdout + p.stderr)
    finally:
        stop.set()
        if sampler.is_alive():
            sampler.join(timeout=15)
        alive = ctl.ssh(f"kill -0 {cpid} 2>/dev/null && echo alive || echo dead",
                        check=False).stdout.strip() == "alive"
        # SIGTERM first so the controller can write its log (codelab step 6 prints its
        # measured priorities on SIGTERM), up to 3 s; then kill -9 whatever of its user is left.
        ctl.ssh(f"kill {cpid} 2>/dev/null; for i in 1 2 3; do kill -0 {cpid} 2>/dev/null || break; sleep 1; done",
                check=False, timeout=30)
        ctl.ssh("kill -9 -1", check=False)          # the controller and anything it started
    fetched = {}
    for name in ["samples.csv", "plant.json", "plant.err", "load.log", "run.json"] + (["monitor.csv", "probe.csv", "ctl_watch.csv"] if monitor else []):
        fetched[name] = tgt.get(f"{remote}/{name}", ev / name)
    if ctl.get(f"{cdir}/controller.log", ev / "controller.log"):
        log_path = ev / "controller.log"
        log_path.write_text(redact(log_path.read_text(errors="replace")))
    result["fetch_failed"] = sorted(k for k, ok in fetched.items() if not ok)
    tgt.ssh(f"rm -rf {remote}", check=False)
    ctl.ssh(f"rm -rf {cdir}", check=False)

    runinfo = json.loads((ev / "run.json").read_text()) if (ev / "run.json").exists() else {}
    active = [p for p, st in prios if st in ("RUNNING", "READY")]
    runinfo.update({"controller_alive": alive, "controller_user": ctl.user,
                    "controller_priority_samples": len(prios),
                    "controller_max_priority": max(p for p, _ in prios) if prios else None,
                    "controller_max_active_priority": max(active) if active else None})
    (ev / "run.json").write_text(json.dumps(runinfo, indent=2))
    return finish(analyze(ev, spec))


def format_report(r: dict) -> str:
    lines = [f"Referee verdict: {r['verdict']}   (run {r['run_id']}, {r.get('elapsed_s', '?')} s)"]
    for c in r.get("checks", []):
        mark = " ok " if c["pass"] else "FAIL"
        lines.append(f"  [{mark}] {c['name']:<12} {c['measured']:<58} limit {c['limit']}")
    s = r.get("stats") or {}
    if s:
        l = s["latency_us"]
        lines.append(f"  latency us: p50 {l['p50']:.1f}  p99 {l['p99']:.1f}  p99.9 {l['p999']:.1f}  max {l['max']:.1f}"
                     f"   tracking rms {s['tracking_rms']:.4f}")
        how = (f"confirmed by prio-{(r.get('plant') or {}).get('monitor_priority')} monitor"
               if s.get("stall_attribution") == "monitor" else "heuristic, no monitor")
        lines.append(f"  target stalls: {s['stalled_samples']} samples excluded ({s['stall_fraction'] * 100:.2f}% of run,"
                     f" limit {s['max_stall_fraction'] * 100:.1f}%; {how}"
                     + (f"; {s['held_by_other']} of them held up by non-controller work" if s.get("held_by_other") else "")
                     + (f"; {s['late_excused']} late replies excused, each fully explained by a pause the monitor saw"
                        if s.get("late_excused") else "")
                     + ")")
    ld = r.get("load") or {}
    if r.get("with_load") and ld:
        lines.append(f"  load: {ld.get('threads')} threads @ prio {ld.get('priority')}, "
                     f"duty {ld.get('duty_actual', 0):.2f} actual on {ld.get('cpus')} CPUs")
    elif not r.get("with_load"):
        lines.append("  load: none")
    if r.get("keep_awake"):
        lines.append("  keep-awake: priority-1 spinner on every CPU (stops idle vCPU halts on VMs)")
    run = r.get("run") or {}
    if run.get("controller_user"):
        lines.append(f"  isolation: controller ran as QNX user '{run['controller_user']}', probes as a different user"
                     + ("; its fork/spawn locked for the run" if (r.get("plant") or {}).get("ctl_spawn_locked") else ""))
    tg = r.get("target") or {}
    if tg:
        lines.append("  target: hardened (qconn root launcher stopped, ssh keys only)" if tg.get("hardened")
                     else f"  target: NOT hardened ({tg.get('reason') or tg}): no PASS possible")
    if r.get("controller_cpu") is not None:
        off = s.get("ctl_offcpu_ticks")
        lines.append(f"  pinning: controller pinned to CPU {r['controller_cpu']} with the plant and monitor"
                     + (f"; WARNING: a controller thread had last run on another CPU on {off} ticks"
                        " (target stalls there are not excused)" if off else "")
                     + ("" if off is not None else " (not measured)"))
    lines.append(f"  spec: {r.get('spec_source')}")
    lines.append(f"  evidence: {r.get('evidence')}" + ("  (signed)" if r.get("signed") else ""))
    return "\n".join(lines)
