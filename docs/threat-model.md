# Threat model and attacks

Referee assumes the agent, and the code it writes, will try anything that makes the verdict PASS. This page lists every attack Referee has been tested against. For each one it says what the attack did before the fix, what stops it now, and which regression test proves it.

To report a new one, see [SECURITY.md](../SECURITY.md).

Each attack below was run against Referee. The "What happened before" column says what it did to the version before its fix. Rows with a result there were real holes found by auditing Referee: the first three against commit `b941c3a`, the rest in a second audit. Every row is now a regression test.

| Attack | What happened before | Defense now | Test |
|---|---|---|---|
| Agent's file tools reach outside the project (Claude Code with bare `Read`/`Write` allow rules) | Claude read and wrote outside the project, including Referee's baseline in `~/.referee` | Tools scoped to `./**` in `-p` mode. Anything else needs an approval nobody can give | `tests/reach_test.py` (judged by `permission_denials` and files on disk, not by the model's words) |
| Makefile rewrites Referee's baseline, then the next round passes | **PASS** for a controller with 19 missed deadlines. It also read the signing key, reached the internet and wrote to `$HOME` | Build runs in a user+mount+net+pid namespace with `$HOME` and `/mnt` hidden. The trusted spec lives in memory only | `tests/run_escape.sh` |
| Controller hides late replies by faking target stalls (priority-63 hog threads timed to the plant's tick) | 5 of 6 late replies hidden as "VM noise" | Priority-120 monitor confirms real stalls. Per-tick procfs watch of every controller thread | `examples/pid/stall_hider` in `tests/run_examples.sh` |
| Same trick from a forked child process | The watch only covered the controller's own process. In the run that checked, all 166 over-ceiling ticks came from the child | Watch every process of the controller's user | `examples/pid/fork_hider` |
| Fork a fresh hog child for each burst, after the watch's startup scan | (closed together with the row above, before it shipped) | fork/spawn locked by `procmgr_ability()` for the whole run. The controller's re-allow call returns OK and changes nothing | `examples/pid/spawn_hider` (its log shows `fork: Operation not permitted`) |
| Agent plants `.claude/settings.json` hooks or a `.mcp.json` server in the workspace, so the next Claude Code round runs its code outside the permission rules | Both ran (negative control in the reach test) | Claude Code started with `--setting-sources user --strict-mcp-config`; those files are always protected | `tests/reach_test.py`, `tests/test_agent_config_guard.py` |
| Build step prints the QNX license (plain, reversed, base64, base64url, hex) into the build log the agent reads | Only verbatim strings were redacted, and only in two logs | Every text file in the evidence is scrubbed before signing, including decoded base64/hex runs | `tests/run_escape.sh`, `tests/test_redact.py` |
| Controller becomes root on the target through `qconn` or an ssh password login | Not exploited. qconn was listening and sshd accepted passwords | Target hardened before every run, and no PASS without it ([details](how-it-works.md#hardened-target)) | every report has a `target:` line. The hardening was checked independently: 0 qconn processes, nothing on port 8000, sshd offers `publickey` only |
| Edit `referee.toml` to loosen limits | - | TAMPER, reverted, round not run | `tests/run_cheater.sh` round 2 |
| Reply instantly with a stale or fake value | - | Every reply checked against Referee's own PID | `bad_stale`, cheater round 3 |
| Kill or read Referee's probes on the target | - | Controller runs as `user1`. Probes run as other users in 700 directories (EPERM, verified) | `tests/run_examples.sh` |
| Reuse a stale binary | - | Artifact deleted before every build. Its sha256 is recorded | all runs |

Edits that look like cheating but don't change a verdict get flagged for a human reviewer: added sleeps, priority changes, `|| true`, Makefile changes, touching Referee paths. The flags are pattern-based, so they inform the reviewer and never decide a verdict.
