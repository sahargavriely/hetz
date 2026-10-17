#!/bin/sh
# Fixture: "toolbox" with three unrelated jobs (CSV delimiter bug, greet --version, a repo question),
# asked as three turns of one session to measure rotation/handoff.
# Usage: setup.sh [DIR]  (default: a new mktemp dir). Prints the fixture path.
set -eu
. "$(dirname "$0")/../_lib.sh"
fixture_dir "${1:-}"

python3 - <<'PYEOF'
import os


def w(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text.lstrip("\n"))


w(".gitignore", "__pycache__/\n")
w("VERSION", "1.4.0\n")
w("README.md", """
# toolbox

Small unrelated command-line helpers.

- `tools/csvstat.py [--delimiter D] FILE`: row/column counts of a CSV file
- `tools/greet.py NAME`: friendly greeting
- `scripts/migrate.py`: one-off data migration
""")
w("data/sample.csv", "id,name,city,zip\n1,Ada,London,N1\n2,Alan,Wilmslow,SK9\n")
w("data/semicolon.csv", "id;name;city\n1;Ada;London\n2;Alan;Wilmslow\n")
w("tools/__init__.py", "")
w("tools/csvstat.py", '''
"""Print row/column counts of a CSV file: csvstat.py [--delimiter D] FILE"""
import argparse
import csv


def stats(path, delimiter=","):
    """(data rows, columns) of a CSV file with a header row."""
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    header, data = rows[0], rows[1:]
    return len(data), len(header)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delimiter", default=",", help="field separator (default ,)")
    ap.add_argument("file")
    args = ap.parse_args(argv)
    rows, cols = stats(args.file)
    print(f"rows={rows} cols={cols}")


if __name__ == "__main__":
    main()
''')
w("tools/greet.py", '''
"""Greet someone: greet.py NAME"""
import argparse


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name")
    args = ap.parse_args(argv)
    print(f"Hello, {args.name}!")


if __name__ == "__main__":
    main()
''')
w("scripts/migrate.py", '''
"""One-off migration: rename the `zip` column to `postcode`."""
import csv
import sys


def migrate(src, dst):
    with open(src, newline="") as f:
        rows = list(csv.reader(f))
    rows[0] = ["postcode" if c == "zip" else c for c in rows[0]]
    with open(dst, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def main():
    migrate(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
''')
w("scripts/notes.md", "Every script exposes main() so it can be wired into a console entry point later.\n")
w("lib/__init__.py", "")
w("lib/runner.py", '''
"""Event loop helpers."""
import time


def main_loop(tick, stop, interval=0.1):
    while not stop():
        tick()
        time.sleep(interval)


def run_main(fn):
    return fn()
''')
w("lib/server.py", '''
"""Toy server object."""


class Server:
    def __init__(self, port=8000):
        self.port = port

    def main(self):
        return f"serving on {self.port}"
''')
w("lib/strings.py", '''
"""String helpers."""


def title_case(s):
    return " ".join(p.capitalize() for p in s.split())
''')
PYEOF

fixture_commit "toolbox fixture"
echo "$DIR"
