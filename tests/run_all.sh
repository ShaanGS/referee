#!/bin/bash
# Host unit tests, then everything that runs on the QNX target. REACH=1 also runs the
# Claude Code sandbox test (one short real Claude Code call).
cd "$(dirname "$0")/.." || exit 1
declare -A res
python3 -m unittest discover -s tests -p 'test_*.py'; res[unit]=$?
bash tests/run_examples.sh; res[examples]=$?
bash tests/run_cheater.sh;  res[cheater]=$?
bash tests/run_escape.sh;   res[escape]=$?
bash tests/run_codelab.sh;  res[codelab]=$?
[ "${REACH:-0}" = 1 ] && { python3 tests/reach_test.py; res[reach]=$?; }
echo; echo "==== summary"; bad=0
for k in "${!res[@]}"; do echo "  $k: $([ ${res[$k]} = 0 ] && echo ok || echo FAILED)"; [ ${res[$k]} = 0 ] || bad=1; done
exit $bad
