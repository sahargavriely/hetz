#!/bin/sh
# Pass = no word `calc_total` left anywhere, look-alikes intact, suite green, new name works.
# Usage: check.sh FIXTURE_DIR [ANSWER_FILE]
set -u
. "$(dirname "$0")/../_lib.sh"
cd "$1" || fail "no fixture dir"
python3 - <<'PYEOF' || fail "rename incomplete or over-eager"
import os, re, sys
left, bad = [], []
for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".claude")]
    for name in files:
        path = os.path.join(root, name)
        text = open(path, errors="replace").read()
        if re.search(r"\bcalc_total\b", text):
            left.append(path)
need = {"shop/shipping.py": "def calc_total_weight(", "shop/reports.py": "def recalc_totals(",
        "shop/pricing.py": "def compute_order_total("}
bad = [p for p, s in need.items() if s not in open(p).read()]
if left or bad:
    print("old name left in:", left, "missing:", bad)
    sys.exit(1)
PYEOF
python3 -m unittest -q >/dev/null 2>&1 || fail "python3 -m unittest is not green"
python3 - <<'PYEOF' || fail "hidden API check"
import sys
sys.path.insert(0, ".")
import shop.pricing as p
assert not hasattr(p, "calc_total"), "alias left behind"
assert p.compute_order_total({"lines": [{"qty": 3, "unit_price": 100}], "promo": "HALF"}, 0.5) == 225
PYEOF
echo PASS
