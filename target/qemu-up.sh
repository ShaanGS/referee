#!/bin/bash
# Boot the QNX SDP 8.0 QEMU quickstart image headless.
# - User-mode networking (no root, no bridge): guest ssh -> 127.0.0.1:${SSH_PORT:-2222}
# - Serial console on a unix socket + log file, so tools can drive/watch it.
set -euo pipefail
IMG="${QNX_IMAGE_DIR:-$HOME/qnxtarget/qemu}"
SSH_PORT="${SSH_PORT:-2222}"
CPUS="${CPUS:-4}"
RAM="${RAM:-4G}"
cd "$IMG"

if [ -f output/qemu.pid ] && kill -0 "$(cat output/qemu.pid)" 2>/dev/null; then
  echo "already running (pid $(cat output/qemu.pid))"; exit 0
fi

if [ -w /dev/kvm ]; then
  # On a bare-metal Linux host, QEMU_CPU_PM="-overcommit cpu-pm=on" lets idle vCPUs
  # halt natively (fewer wake-up stalls). It hangs the guest under WSL2's nested
  # virtualization, so it is off by default.
  ACCEL=(-enable-kvm -cpu host ${QEMU_CPU_PM:-})
else
  ACCEL=(-cpu max); echo "WARNING: no KVM, slow"
fi

rm -f output/qemu.out output/serial.sock
qemu-system-x86_64 \
  "${ACCEL[@]}" -smp "$CPUS" -m "$RAM" \
  -drive file=output/disk-qemu.vmdk,if=ide,id=drv0 \
  -kernel output/ifs.bin \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 \
  -device virtio-net-pci,netdev=net0,mac=52:54:00:cf:bf:ed \
  -object rng-random,filename=/dev/urandom,id=rng0 -device virtio-rng-pci,rng=rng0 \
  -chardev socket,id=ser0,path=output/serial.sock,server=on,wait=off,logfile=output/qemu.out \
  -serial chardev:ser0 \
  -display none -monitor none \
  -pidfile output/qemu.pid -daemonize

echo "QNX booting (pid $(cat output/qemu.pid)); console log: $IMG/output/qemu.out; ssh port $SSH_PORT"
