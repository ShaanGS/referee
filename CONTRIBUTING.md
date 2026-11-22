# Contributing

Thanks for looking. The most valuable contribution is **a new way to fool Referee**: if you find one, see [SECURITY.md](SECURITY.md) first.

## Setup

You need:

- Linux or WSL2 (tested on Ubuntu 24.04) with unprivileged user namespaces (for the build sandbox);
- [QNX SDP 8.0](https://www.qnx.com/products/everywhere/) (free non-commercial license) installed at `~/qnx800`, or set `QNX_SDP`;
- the QNX 8.0 x86_64 QEMU quickstart image; [target/README.md](target/README.md) has the steps;
- Python 3.11+. The host side uses only the standard library.

```bash
sh bin/referee target up          # boot the target (keep its console open on WSL)
sh bin/referee target status
sh bin/referee check --project examples/pid/good
```

## Tests

| Command | Needs | What it covers |
|---|---|---|
| `python3 -m unittest discover -s tests -p 'test_*.py'` | nothing | analysis edge cases, stall excuses, tamper guard, signing, license redaction, agent config protection |
| `bash tests/run_examples.sh` | target | reference and adversarial controllers give the expected verdicts |
| `bash tests/run_cheater.sh` | target | scripted cheating agent: FAIL, TAMPER, FAIL, PASS |
| `bash tests/run_escape.sh` | target | hostile build step is contained |
| `bash tests/run_codelab.sh` | target | every codelab step behaves as the tutorial says |
| `REACH=1 bash tests/run_all.sh` | target + Claude Code | all of the above plus a real Claude Code sandbox test |

CI runs the first row plus lint and evidence verification on every push. Please run `bash tests/run_all.sh` locally before a pull request that touches `probes/`, `referee/check.py` or the sandbox.

## Ground rules

- **Never loosen a check to make a test pass.** If an honest controller fails, find out why (the git history has examples: CPU pinning, the stall monitor) and fix the cause.
- **Every attack gets a regression test.** That can be an adversarial example under `examples/pid/`, a scripted agent under `examples/pid/scenarios/`, or a unit test.
- **Claims in docs need a source.** Numbers in the README come from real runs, and the run or test that produced them should be findable.
- Don't commit QNX SDP files, target images or license files (`.gitignore` covers the usual paths).
