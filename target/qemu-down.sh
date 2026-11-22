#!/bin/bash
IMG="${QNX_IMAGE_DIR:-$HOME/qnxtarget/qemu}"
if [ -f "$IMG/output/qemu.pid" ] && kill "$(cat "$IMG/output/qemu.pid")" 2>/dev/null; then
  echo "stopped"; rm -f "$IMG/output/qemu.pid"
else
  echo "not running"
fi
