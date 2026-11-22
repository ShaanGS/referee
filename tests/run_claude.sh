#!/bin/bash
# Real Claude Code against a fresh copy of the demo project, kept OUTSIDE this repo
# so the agent can't find the example solutions. Uses your own Claude login.
cd "$(dirname "$0")/.." || exit 1
ROOT="${WORK_ROOT:-$(cd .. && pwd)/referee-work}"
W="$ROOT/claude-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ROOT" && cp -r examples/pid/agent_demo "$W"
echo "workspace: $W"
sh bin/referee agent --project "$W" --agent claude --rounds "${ROUNDS:-5}" ${DURATION:+--duration $DURATION}
rc=$?
PACK=$(ls -d "$W"/evidence/agent-* 2>/dev/null | tail -1)
[ -n "$PACK" ] && { echo "== verify:"; sh bin/referee verify "$PACK"; }
exit $rc
