#!/bin/sh
# Pass = visible suite green, tests/ untouched, and hidden rounding cases correct.
# Usage: check.sh FIXTURE_DIR [ANSWER_FILE]
set -u
. "$(dirname "$0")/../_lib.sh"
cd "$1" || fail "no fixture dir"
unchanged tests || fail "tests/ was modified"
python3 -m unittest -q >/dev/null 2>&1 || fail "python3 -m unittest is not green"
python3 - <<'EOF' || fail "hidden rounding cases"
import sys
sys.path.insert(0, ".")
from ledger.money import round_half_up as r
cases = {(5, 2): 3, (-5, 2): -3, (7, 3): 2, (-7, 3): -2, (1, 3): 0, (2, 3): 1, (-2, 3): -1,
         (0, 7): 0, (15, 10): 2, (-15, 10): -2, (14, 10): 1, (-14, 10): -1, (10**12 + 5, 10): 10**11 + 1}
bad = {k: (r(*k), v) for k, v in cases.items() if r(*k) != v}
if bad:
    print("wrong:", bad)
    sys.exit(1)
EOF
echo PASS
