#!/bin/bash
# Runs every codelab step (codelab/README.md) on the QNX target and checks the
# verdicts and log lines the codelab promises. Same flow a learner follows:
# copy the starter project, drop in each step's controller, run `referee check`.
cd "$(dirname "$0")/.." || exit 1
W="work/codelab-$(date +%H%M%S)"
mkdir -p work && cp -r examples/pid/agent_demo "$W"
R="sh bin/referee check --project $W ${DURATION:+--duration $DURATION}"
fails=0

step() {  # step <name> <controller.c or -> <want verdict> [grep pattern in output] [grep pattern in controller.log]
  name="$1" src="$2" want="$3" pat="$4" logpat="$5"
  [ "$src" != - ] && cp "$src" "$W/src/controller.c"
  echo "==== $name"
  out="$($R)"; echo "$out"
  got="$(echo "$out" | sed -n 's/^Referee verdict: \([A-Z]*\).*/\1/p')"
  ok=1
  if [ "$got" = "$want" ] || { [ "$want" = PASS ] && [ "$got" = INCONCLUSIVE ]; }; then :; else ok=0; fi
  [ -n "$pat" ] && ! echo "$out" | grep -q -- "$pat" && { echo ">>> missing in output: $pat"; ok=0; }
  if [ -n "$logpat" ]; then
    log="$(ls -d "$W"/evidence/*/ | tail -1)controller.log"
    echo "-- controller.log:"; cat "$log"
    IFS='|' read -ra pats <<< "$logpat"     # several required lines, separated by |
    for lp in "${pats[@]}"; do
      grep -q -- "$lp" "$log" || { echo ">>> missing in controller.log: $lp"; ok=0; }
    done
  fi
  if [ $ok = 1 ]; then echo ">>> $name: expected $want, got $got: OK"
  else echo ">>> $name: expected $want, got $got: MISMATCH"; fails=$((fails + 1)); fi
  echo
}

step "step 3 (starter stub)"  -                                FAIL
step "step 4 (echo u=0)"      codelab/steps/step4_echo.c       FAIL '\[FAIL\] control law'
step "step 5 (PID)"           codelab/steps/step5_pid.c        PASS
step "step 6 (inheritance)"   codelab/steps/step6_inherit.c    PASS '' 'before any message: running at 10|handling sample 2000: running at 40'
step "step 7 (worker)"        codelab/steps/step7_worker.c     FAIL '\[FAIL\] deadline' 'worker replying to sample [0-9]*: running at 10'
echo "codelab mismatches: $fails"
exit $fails
