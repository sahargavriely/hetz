#!/bin/sh
# Pass = all three turns done: csvstat honours --delimiter, greet --version reads VERSION,
# and the final answer lists exactly the files defining a top-level main().
# Usage: check.sh FIXTURE_DIR ANSWER_FILE (ANSWER_FILE = the last turn's reply)
set -u
. "$(dirname "$0")/../_lib.sh"
ANS=$(abspath "${2:-}")
cd "$1" || fail "no fixture dir"
FIX=$(pwd)
[ "$(python3 tools/csvstat.py --delimiter ';' data/semicolon.csv 2>&1)" = "rows=2 cols=3" ] || fail "turn 1: --delimiter"
[ "$(python3 tools/csvstat.py data/sample.csv 2>&1)" = "rows=2 cols=4" ] || fail "turn 1: default delimiter broke"
[ "$(python3 tools/greet.py Ada 2>&1)" = "Hello, Ada!" ] || fail "turn 2: greet NAME broke"
[ "$(python3 tools/greet.py --version 2>&1)" = "greet 1.4.0" ] || fail "turn 2: --version"
cp VERSION VERSION.bench-orig && printf '2.0.0\n' > VERSION
out=$(cd / && python3 "$FIX/tools/greet.py" --version 2>&1)
mv VERSION.bench-orig VERSION
[ "$out" = "greet 2.0.0" ] || fail "turn 2: version is not read from VERSION (or depends on cwd)"
[ -s "$ANS" ] || fail "turn 3: empty answer"
python3 - "$ANS" <<'PYEOF' || fail "turn 3: wrong file list"
import sys
text = open(sys.argv[1], errors="replace").read()
want = ["tools/csvstat.py", "tools/greet.py", "scripts/migrate.py"]
avoid = ["lib/runner.py", "lib/server.py"]
missing = [p for p in want if p not in text]
extra = [p for p in avoid if p in text]
if missing or extra:
    print("missing:", missing, "wrong:", extra)
    sys.exit(1)
PYEOF
echo PASS
