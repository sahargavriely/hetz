#!/bin/sh
# Pass = `make test` green, tests/tools/Makefile untouched, hidden slug/wrap cases correct.
# Usage: check.sh FIXTURE_DIR [ANSWER_FILE]
set -u
. "$(dirname "$0")/../_lib.sh"
cd "$1" || fail "no fixture dir"
unchanged tests tools Makefile || fail "tests/, tools/ or Makefile was modified"
make test >/dev/null 2>&1 || fail "make test is not green"
python3 - <<'PYEOF' || fail "hidden slug/wrap cases"
import sys
sys.path.insert(0, ".")
from textkit.slug import slugify
from textkit.wrap import wrap_text
checks = [
    (slugify("Ça va? Déjà vu!"), "ca-va-deja-vu"),
    (slugify("naïve façade", sep="_"), "naive_facade"),
    (wrap_text("aa bb cc", 5), ["aa bb", "cc"]),
    (wrap_text("a", 1), ["a"]),
    (wrap_text("aaa bbb ccc", 7), ["aaa bbb", "ccc"]),
]
bad = [(got, exp) for got, exp in checks if got != exp]
if bad:
    print("wrong:", bad)
    sys.exit(1)
PYEOF
echo PASS
