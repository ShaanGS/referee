# How a run works

```
host (WSL2)                                QNX 8.0 target
-----------                                ----------------------------------------------
sandboxed build (no net, no $HOME)  --->   user1:   controller (the agent's code), CPU 0
                                                      ^ MsgSend / MsgReply every 1 ms
                                           root:    referee_plant  prio 40, CPU 0
                                                    + monitor thread prio 120, CPU 0
                                                      (timestamps every tick; reads the
                                                       state/priority of every thread of
                                                       every user1 process from procfs on
                                                       every tick; user1's fork/spawn locked)
                                           qnxuser: referee_load   prio 20, all CPUs
                                                    keep-awake     prio 1 (VM only)
raw CSVs  <---------------------------------------  samples.csv, monitor.csv, ctl_watch.csv
verdict + signed evidence + report.html
```

## The checks

Missed deadlines (late, skipped, errors, held-up periods), p99 latency, the control law on every reply, no crash, and a priority ceiling. For the ceiling, no thread of the controller's user, in any of its processes, may run above the plant's priority on any tick.

## No new processes

 When the plant starts it finds every process of the controller's user and locks fork and spawn for each one with `procmgr_ability()`: deny, lock, inherit. QNX then returns EPERM for `fork()` and `posix_spawn()`, and the controller can't undo it. On QNX 8.0 a later re-allow call returns success but changes nothing. `spawn_hider` checks exactly that. Every 50 ms the plant scans again for processes that appeared before the lock, and a verdict fails if any are found. The watch runs at the start of each tick on CPU 0, before the plant sends, so it never overlaps a reply. Measured cost: a mean of 9 µs per tick for a one-process controller and up to 26 µs for the hiders. On a noisy host a single watch occasionally took up to ~3 ms, and correct controllers still had 0 misses. It is capped at 64 threads / 32 processes, and a controller over the cap fails the ceiling check instead of going unwatched.

## Hardened target

 QNX's quickstart image runs `qconn`, an unauthenticated launcher that starts any program as root, and its sshd accepts passwords. Code that becomes root could stop the monitor or edit the plant's measurements. So before every run Referee stops qconn through its supervisor (`slmctl stop qconn`, so it isn't restarted), kills strays, turns ssh password logins off (`PasswordAuthentication no`, original config kept as `sshd_config.referee-orig`), and then checks the result from the outside, by asking sshd which login methods it offers. A target that isn't hardened can't get PASS. Its best verdict is INCONCLUSIVE, and the report says why.

## Target stalls

 QEMU vCPUs get paused by the host. A sample only counts as a stall if Referee's priority-120 monitor, on the same CPU as the plant, saw the gap too. Unprivileged QNX threads stop at priority 63, so the agent's code cannot cause such a gap. Stalled samples are excluded and reported. Over 1% of the run (`max_stall_fraction`, measured basis in [limits](limits.md)) makes the verdict INCONCLUSIVE, and only for failures the noise could plausibly explain: never more missed deadlines, or more clean replies over the p99 limit, than three times the stalled samples (at least 10), and never a p99 over twice its limit. The second count matters because a controller whose replies sit just under the deadline collects reply-probe excuses of its own making; without it, one that was slow on every single reply got INCONCLUSIVE instead of FAIL (found by the planted-bug benchmark, `bug_work_after_reply`). The rule is `decide()` in `referee/check.py`, unit-tested in `tests/test_verdict.py`. An excused sample excuses only what the pause explains, plus the monitor's 1.5 ms resolution: a controller that blocks the plant for seconds still pays for every period, even if a short pause lands inside the block. Periods the plant never got to send at all also count as missed.

## One CPU for the whole loop

 The controller is started with `on -C 0`, so it and every thread it creates share CPU 0 with the plant and the monitor. Without this, a correct controller that the scheduler happened to place on another CPU could pick up delays the monitor never sees. In testing, the same correct PID code moved to CPU 1 missed exactly 9 deadlines in each of two 10 s runs (about one per second, cause not identified), and none when pinned. Each verdict reports whether a controller thread ran anywhere else. Moving off CPU 0 can't help a cheater: delays the monitor can't see always count against the controller.
