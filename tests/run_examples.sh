#!/bin/bash
# Run every PID example and check Referee reaches the expected verdict.
#   good        -> PASS under load
#   bad_worker  -> idle: never PASS. Its priority-10 worker misses a few dozen
#                  deadlines; on a noisy host (stalls > 0.5%) Referee may call that
#                  INCONCLUSIVE instead of FAIL. Under load: FAIL badly.
#   bad_stale   -> FAIL under load (control law)
#   stall_hider -> FAIL on the priority ceiling: it hides late replies by faking
#                  target stalls with priority-63 hog threads (adversarial example)
#   fork_hider  -> same cheat, but the hogs live in a child forked at startup: still FAIL
#                  on the priority ceiling (Referee watches every process the controller's
#                  user owns, not just the controller)
#   spawn_hider -> forks a fresh hog child per burst, at run time: Referee has locked
#                  fork/spawn for the run, so every fork fails (EPERM, in controller.log),
#                  no hog runs, and the late replies count: never PASS (FAIL on a quiet
#                  host; a noisy host may make 6 misses INCONCLUSIVE)
cd "$(dirname "$0")/.." || exit 1
R="sh bin/referee check --duration ${DURATION:-10}"
fails=0
expect() {  # expect <verdict> <args...>
  want="$1"; shift
  out="$($R "$@")"   # global: read by the extra check after stall_hider
  got="$(echo "$out" | sed -n 's/^Referee verdict: \([A-Z]*\).*/\1/p')"
  echo "$out"
  # PASS also accepts INCONCLUSIVE (noisy host); NOT_PASS accepts FAIL or INCONCLUSIVE.
  if [ "$got" = "$want" ] || { [ "$want" = PASS ] && [ "$got" = INCONCLUSIVE ]; } \
     || { [ "$want" = NOT_PASS ] && { [ "$got" = FAIL ] || [ "$got" = INCONCLUSIVE ]; }; }; then
    echo ">>> expected $want, got $got: OK"
  else
    echo ">>> expected $want, got $got: MISMATCH"; fails=$((fails + 1))
  fi
  echo
}
expect PASS --project examples/pid/good
expect PASS --project examples/pid/good --no-load
expect NOT_PASS --project examples/pid/bad_worker --no-load
expect FAIL --project examples/pid/bad_worker
expect FAIL --project examples/pid/bad_stale
expect FAIL --project examples/pid/stall_hider
if echo "$out" | grep -q '\[FAIL\] prio ceiling'; then echo ">>> stall_hider caught by the priority ceiling: OK"
else echo ">>> stall_hider NOT caught by the priority ceiling: MISMATCH"; fails=$((fails + 1)); fi
expect FAIL --project examples/pid/fork_hider
if echo "$out" | grep -q '\[FAIL\] prio ceiling'; then echo ">>> fork_hider caught by the priority ceiling: OK"
else echo ">>> fork_hider NOT caught by the priority ceiling: MISMATCH"; fails=$((fails + 1)); fi
expect NOT_PASS --project examples/pid/spawn_hider
ev="$(echo "$out" | sed -n 's/^  evidence: \([^ ]*\).*/\1/p')"
if grep -q "fork: Operation not permitted" "$ev/controller.log" 2>/dev/null \
   && grep -q '"ctl_new_procs":0' "$ev/plant.json" 2>/dev/null; then
  echo ">>> spawn_hider's run-time forks were blocked by the fork/spawn lock: OK"
else echo ">>> spawn_hider's forks were NOT blocked: MISMATCH"; fails=$((fails + 1)); fi
echo "mismatches: $fails"
exit $fails
