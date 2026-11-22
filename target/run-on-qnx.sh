#!/bin/bash
# Build a C file for QNX x86_64, copy it to the QEMU target, run it, return its exit code.
# usage: run-on-qnx.sh <source.c> [args...]
set -euo pipefail
SRC="$1"; shift || true
SDP="${QNX_SDP:-$HOME/qnx800}"
KEY="${REFEREE_KEY:-$HOME/.ssh/referee_qnx}"
PORT="${SSH_PORT:-2222}"
USER_HOST="${TARGET:-qnxuser@127.0.0.1}"
SSH_OPTS=(-i "$KEY" -o UserKnownHostsFile="$HOME/.ssh/known_hosts_referee" -o BatchMode=yes -o ConnectTimeout=10)

# shellcheck disable=SC1091
source "$SDP/qnxsdp-env.sh" >/dev/null
OUT="$(mktemp -d)/$(basename "${SRC%.c}")"
qcc -Vgcc_ntox86_64 -O2 -Wall -o "$OUT" "$SRC"
echo "[host] built $(basename "$OUT") ($(stat -c %s "$OUT") bytes) with qcc gcc_ntox86_64"

REMOTE="/tmp/referee-$(basename "$OUT")-$$-$(date +%s)"
scp -q -P "$PORT" "${SSH_OPTS[@]}" "$OUT" "$USER_HOST:$REMOTE"
echo "[host] copied to $USER_HOST:$REMOTE, running..."
set +e
ssh -p "$PORT" "${SSH_OPTS[@]}" "$USER_HOST" "chmod +x $REMOTE && $REMOTE $*; rc=\$?; rm -f $REMOTE; exit \$rc"
rc=$?
set -e
rm -rf "$(dirname "$OUT")"
echo "[host] exit code from QNX: $rc"
exit $rc
