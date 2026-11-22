# Security policy

Referee is a judge for code written by AI agents. Its job is to give a wrong verdict to nobody, so **a way to make Referee give PASS to code that doesn't meet its deadline is a security bug**, as is a way to read or change anything outside the workspace from an agent's edits or build step.

## Reporting

Open a [private security advisory](https://github.com/ShaanGS/referee/security/advisories/new) on this repository. Please include:

- what the attack does (for example "PASS with N missed deadlines", "reads `~/.referee/signing_key`");
- the smallest project or controller that shows it (a `referee.toml`, `src/`, `Makefile`);
- the evidence folder of a run, if you have one.

You'll get a reply within a few days. Confirmed attacks get a fix and a regression test, and the README's attack table credits you if you want.

## What's in scope

| In scope | Examples |
|---|---|
| Verdict forgery | PASS (or INCONCLUSIVE instead of FAIL) for a controller that misses deadlines, replies with wrong values, or runs above the plant's priority |
| Escaping the agent sandbox | an agent edit or a build step that reaches outside the workspace copy: `~/.referee`, `~/.ssh`, the network, the Referee repo |
| Evidence tampering | changing a signed evidence folder so that `referee verify` still says OK |
| Target escapes | the controller (QNX user `user1`) gaining root, stopping the monitor, or editing the plant's measurements |
| Secret leaks | the QNX license or Referee's signing key reaching anything the agent can read |

## Known limits (not bugs, documented)

These are listed in the README's known limits, with the reasons: QEMU-only timing, the short startup window before fork/spawn is locked, the 64-thread / 32-process watch cap, best-effort license redaction, local (not identity-proving) signatures, and the benefit of the doubt (up to the monitor's 1.5 ms resolution) given inside excused stalls.
