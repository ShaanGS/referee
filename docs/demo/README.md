# Demo evidence

Real output from Referee, committed so you can look at it and check it without QNX. Everything here was produced on 22 Nov 2026 by the code in this repo, on QNX 8.0 in QEMU (WSL2, Windows 11).

| Path | What it is | Signed? |
|---|---|---|
| [`evidence-pass/`](evidence-pass/) | The complete evidence folder of one run of `examples/pid/good` (run `20261122-050048`): raw samples, monitor gaps, controller watch, build log, verdict, report. Verdict PASS: 0 missed of 9,500, p99 4.0 µs. | Yes, ed25519 |
| [`referee-signer.pub`](referee-signer.pub) | The public half of the key that signed `evidence-pass/` | |
| [`agent-run/`](agent-run/) | The report pages of a four-round agent run (`agent-20261122-051311`): FAIL, TAMPER, FAIL, PASS. The agent is the labelled **scripted** cheater from `examples/pid/scenarios/cheater`, not a live model. | No, report pages only |
| [`caught-stall-hider/`](caught-stall-hider/) | The report of `examples/pid/stall_hider` (run `20261122-050030`): latency looks fine, but 168 ticks above the plant's priority and 50 missed deadlines | No, report page only |

## Check the signature

```bash
sh bin/referee verify docs/demo/evidence-pass --signer docs/demo/referee-signer.pub
```

`verify` re-hashes every file listed in `manifest.json`, checks that no file was added or removed, and checks the signature with `ssh-keygen -Y verify`. It prints `OK: signature valid and all files match` and exits 0. Change, add or remove any file and it exits 1. CI runs both cases on every push ([`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)).

What a valid signature proves: the folder is byte-for-byte what Referee sealed when the run ended. What it doesn't prove: who ran it. The key is local to the machine that ran Referee (see [known limits](../limits.md)).

## Read the raw data

| File | Contents |
|---|---|
| `samples.csv` | One row per plant sample: sequence number, period, status, send and reply times and latency in `ClockCycles()`, the plant state, the controller's reply `u_reply` and Referee's reference `u_ref` |
| `monitor.csv` | Every gap the priority-120 monitor saw in its own ticking (`from_cyc,to_cyc`). A late sample is excused as a VM stall only if it overlaps one of these |
| `ctl_watch.csv` | Every tick on which the procfs watch saw a thread of the controller's user running above the plant's priority. Empty here, because the controller never did (compare the 168 rows behind the `stall_hider` report) |
| `probe.csv` | Wake-ups of the reply probe, a high-resolution timer the plant arms for the monitor around each send. For a slow reply they show how much of the wait the CPU was unavailable even at priority 120 (pauses shorter than a 1 ms tick) |
| `verdict.json` | The verdict and every number behind it. The HTML report is drawn from this |
| `spec.json` | The limits the run was judged against (Referee's in-memory copy, not the workspace file) |
| `build.log`, `controller.log`, `target.log` | What the build, the controller and the target setup printed (license strings scrubbed before signing) |
| `manifest.json`, `manifest.json.sig`, `signer.pub` | File hashes, their signature, and the key that made it |

To redraw the report from the raw data: `sh bin/referee report docs/demo/evidence-pass` (this rewrites `report.html`, so do it on a copy if you want the signature to stay valid).
