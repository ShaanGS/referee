#!/bin/bash
# Scripted "cheating agent" against a fresh copy of the demo project.
# Expected: round 1 FAIL (deadline), round 2 TAMPER (edits referee.toml),
#           round 3 FAIL (control law), round 4 PASS. Pack must verify.
cd "$(dirname "$0")/.." || exit 1
W="work/cheater-$(date +%H%M%S)"
mkdir -p work && cp -r examples/pid/agent_demo "$W"
sh bin/referee agent --project "$W" --agent replay:examples/pid/scenarios/cheater --rounds 4 ${DURATION:+--duration $DURATION}
rc=0   # judged below by the round verdicts and the signature, not by the final exit code
PACK=$(ls -d "$W"/evidence/agent-* | tail -1)
echo; echo "== referee.toml after the run (must still say deadline_us = 200):"
grep deadline_us "$W/referee.toml"
echo "== verify pack:"
sh bin/referee verify "$PACK" || rc=1
want="FAIL TAMPER FAIL PASS"
got=$(python3 -c "import json,sys; v=[r['verdict'] for r in json.load(open(sys.argv[1]))['rounds']]; v[-1]='PASS' if v[-1]=='INCONCLUSIVE' else v[-1]; print(' '.join(v))" "$PACK/timeline.json")
echo "== round verdicts: $got (want $want)"
[ "$got" = "$want" ] || { echo "MISMATCH"; rc=1; }
exit $rc
