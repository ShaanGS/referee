#!/bin/bash
# Boot the QNX VM (if needed) and follow its console until it stops.
# On WSL this attached session is what keeps Ubuntu (and the VM) alive.
IMG="${QNX_IMAGE_DIR:-$HOME/qnxtarget/qemu}"
bash "$(dirname "$0")/qemu-up.sh" || exit 1
PID="$(cat "$IMG/output/qemu.pid")"
echo "following console (Ctrl-C stops following; run qemu-down.sh to stop the VM)"
tail --pid="$PID" -n +1 -f "$IMG/output/qemu.out"
echo "VM stopped"
