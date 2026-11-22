# Known limits

The honest list. Each one is also why a verdict can be INCONCLUSIVE rather than PASS.

- **Timing is from QEMU on a desktop.** Absolute numbers are host-dependent, and real numbers need real hardware. Referee has not yet run on a physical board.
- **Processes created before the lock.** The controller starts a moment before the plant locks its fork/spawn. A child forked in that window is found by the startup scan or by a rescan within 50 ms, and fails the run. A child that forks, runs and exits entirely between two scans, inside that startup window, could go unseen. The watch is also capped (64 threads / 32 processes); over the cap is a FAIL, never a silent gap.
- **The fork/spawn lock relies on QNX 8.0 `procmgr_ability()` behaviour** that was measured on the target, not taken from documentation. A locked deny can't be lifted, and a re-allow call reports success without effect. `spawn_hider` re-checks this on every suite run.
- **Referee changes the target.** Each run stops `qconn` and turns off ssh password logins on the VM (backup kept as `/etc/ssh/sshd_config.referee-orig`). That's intended for a test VM. Don't point Referee at a machine where you need either one.
- **License redaction is best effort.** `qcc` needs the QNX license, so the build can read it. Referee scrubs it from everything the agent can read: verbatim, reversed, base64, base64url and hex. A build that encodes it some other way (XOR, its own alphabet, numbers in the replies) could still get it out. Treat the agent as able to see your license.
- **Claude Code doesn't load the workspace `CLAUDE.md`.** Turning off workspace settings so planted hooks can't run turns that off too. Put task instructions in the task file Referee passes in the prompt.
- **The stall monitor needs `sudo -n` for the probe user** on the target (true for `qnxuser` on the QNX quickstart image). Without it Referee falls back to a weaker heuristic and says so in the verdict.
- **The build sandbox needs unprivileged user namespaces** (tested on WSL2 Ubuntu 24.04). The build sees a private copy of the QNX license file.
- **Agent confinement is backend-specific.** For Claude Code it relies on Claude Code's permission rules, which the reach test checks. Another agent needs its own sandbox.
- **Signing proves integrity, not identity.** The key is local, so a valid signature shows the folder is unchanged since it was signed, not who ran it.
- **A noisy host gives INCONCLUSIVE, often.** Background load on the Windows host (OneDrive sync, for example) pauses the VM. Over 0.5% of samples stalled means INCONCLUSIVE for any failure the noise could explain. On a busy day on the development machine, that happened in 3 of 8 suite runs. It's never a false PASS, but record demos on a quiet host.
- **Rare false FAILs on a noisy host.** The monitor ticks every 1 ms, so a host hiccup shorter than a tick can fall between ticks unseen. Referee then blames the controller rather than excuse it. On the development machine (Windows 11, WSL2), a correct controller failed this way in 2 of 27 runs (1 late reply each time). The real timing bugs tested missed dozens to thousands of deadlines, every run.
- **The controller is confined to one CPU** (see [How a run works](how-it-works.md#one-cpu-for-the-whole-loop)). That suits a single control loop, but a design that relies on spreading work across cores can't be judged fairly yet.
- One task so far: a single 1 kHz PID loop.
