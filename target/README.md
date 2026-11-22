# QNX target (QEMU)

Host: WSL2 Ubuntu 24.04 on Windows 11, KVM enabled. QNX SDP 8.0 in `~/qnx800` (installed with QNX Software Center CLI, non-commercial license). Quickstart image unpacked to `~/qnxtarget/qemu` (50.8 GB sparse-ish disk, ~48 GB on disk).

| Script | What it does |
|---|---|
| `qemu-up.sh` | Boots the quickstart image headless with KVM, 4 CPUs, 4 GB. User-mode networking, guest ssh forwarded to `127.0.0.1:2222`. Serial console on `output/serial.sock`, logged to `output/qemu.out`. |
| `qemu-down.sh` | Stops it. |
| `install_key.py` | One-time: logs in on the serial console with the quickstart default test account and installs an SSH public key. |
| `run-on-qnx.sh <file.c> [args]` | Builds with `qcc -Vgcc_ntox86_64`, scp's to a unique `/tmp/referee-*` path, runs it, deletes it, returns its exit code. |

```bash
bash target/qemu-up.sh                       # boot (~25 s to login prompt)
bash target/run-on-qnx.sh examples/hello/hello.c 7; echo $?   # -> 7
bash target/qemu-down.sh
```

SSH: `ssh -i ~/.ssh/referee_qnx -p 2222 -o UserKnownHostsFile=~/.ssh/known_hosts_referee qnxuser@127.0.0.1`

## First measurement (21 Nov 2026)

`hello.c` times 1000 x `nanosleep(1 ms)` with `ClockCycles()` (3.36 GHz):
mean overshoot ~1000 us, worst ~1.5-2.2 ms. A 1 ms sleep really takes ~2 ms because QNX rounds sleeps up to the next 1 ms tick plus one. This is the "looks right, isn't" behaviour Referee's demo round 1 relies on.

## Notes
- Headless: the image's Screen/graphics/demolauncher startup fails by design; ignore those console errors.
- The stock `mkqnximage --run` path wants a root bridge (`virbr0`); we avoid that with QEMU user networking.
