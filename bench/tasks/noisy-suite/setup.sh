#!/bin/sh
# Fixture: "textkit" with a noisy `make test` (coloured build log with progress bars, ~350 verbose test
# lines) and 3 failures from 2 root causes (slug accent folding, wrap exact-fit off-by-one).
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

w("README.md", """
# textkit

Small text utilities plus 40 transform plugins.

    make test     # build (byte-compile) + run the whole suite
""")
w(".gitignore", "build/\n__pycache__/\n")
w("Makefile", "PY ?= python3\n\n.PHONY: build test clean\n\nbuild:\n\t$(PY) tools/build.py\n\n"
  "test: build\n\t$(PY) -m unittest -v\n\nclean:\n\trm -rf build\n")

w("textkit/__init__.py", '"""textkit: text helpers."""\n')
w("textkit/slug.py", '''
"""URL slugs."""
import re


def slugify(text, sep="-"):
    """'Crème Brûlée!' -> 'creme-brulee'. Folds accents to ASCII, lowercases, joins words with sep."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", sep, text)
    return text.strip(sep)
''')
w("textkit/wrap.py", '''
"""Greedy word wrapping."""


def wrap_text(text, width):
    """Split text into lines of at most `width` chars; a word longer than width gets its own line."""
    lines, cur = [], ""
    for word in text.split():
        candidate = f"{cur} {word}" if cur else word
        if len(candidate) < width:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines
''')
w("textkit/case.py", '''
"""Identifier case conversion."""
import re


def snake(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", name)
    return re.sub(r"[-\\s]+", "_", name).lower()


def camel(name):
    head, *rest = snake(name).split("_")
    return head + "".join(p.title() for p in rest)
''')
w("textkit/stats.py", '''
"""Word statistics."""
from collections import Counter


def word_count(text):
    return len(text.split())


def top_words(text, n=3):
    counts = Counter(w.lower().strip(".,!?") for w in text.split())
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]
''')

w("textkit/plugins/__init__.py", '"""Transform plugins, one per module."""\n')
for i in range(40):
    w(f"textkit/plugins/mod_{i:02d}.py", f'''
"""Plugin {i:02d}: rotate letters by {i % 26} and tag the result."""

SHIFT = {i % 26}
TAG = "p{i:02d}"


def transform(text):
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - 97 + SHIFT) % 26 + 97))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - 65 + SHIFT) % 26 + 65))
        else:
            out.append(ch)
    return TAG + ":" + "".join(out)
''')

w("tools/build.py", '''
"""Byte-compile textkit into build/ with a (deliberately chatty) progress log."""
import os
import py_compile
import sys
import time

COLOR = not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


def c(code, s):
    return f"\\033[{code}m{s}\\033[0m" if COLOR else s


def main():
    files = []
    for root, _, names in os.walk("textkit"):
        files += [os.path.join(root, n) for n in sorted(names) if n.endswith(".py")]
    files.sort()
    print(c("1;34", "== textkit build =="))
    for k, v in [("python", sys.version.split()[0]), ("platform", sys.platform), ("sources", len(files)),
                 ("output", "build/"), ("optimize", 0), ("jobs", 1)]:
        print(f"  {k:<10} {v}")
    print(c("2", "Scanning dependencies of target textkit"))
    total = len(files)
    for n, src in enumerate(files, 1):
        pct = n * 100 // total
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        sys.stdout.write(f"\\r[{bar}] {pct:3d}%")
        sys.stdout.flush()
        dst = os.path.join("build", src + "c")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        py_compile.compile(src, cfile=dst, doraise=True)
        print()
        print(c("32", f"[{pct:3d}%] Building PYC object {dst}"))
        print(c("2", f"        source {src} ({os.path.getsize(src)} bytes)"))
        print(c("2", f"        checking imports for {src} ... ok"))
        if n % 9 == 0:
            print(c("33", f"DeprecationWarning: build option 'legacy_layout' is deprecated (seen in {src})"))
    print(c("1;32", f"[100%] Built target textkit ({total} modules)"))


if __name__ == "__main__":
    main()
''')

w("tests/__init__.py", "")
plug = ['import importlib', 'import unittest', '', '', 'class PluginTest(unittest.TestCase):', '    pass', '', '']
samples = ["hello", "World", "abc xyz", "Zebra-42", "", "The quick brown fox", "MiXeD cAsE", "a.b,c!"]
for i in range(40):
    shift = i % 26
    for j, s in enumerate(samples):
        exp = "".join(chr((ord(ch) - 97 + shift) % 26 + 97) if "a" <= ch <= "z" else
                      chr((ord(ch) - 65 + shift) % 26 + 65) if "A" <= ch <= "Z" else ch for ch in s)
        plug.append(f"def _t_{i:02d}_{j}(self):\n"
                    f"    m = importlib.import_module('textkit.plugins.mod_{i:02d}')\n"
                    f"    self.assertEqual(m.transform({s!r}), {('p%02d:' % i) + exp!r})\n\n\n"
                    f"PluginTest.test_mod_{i:02d}_sample_{j} = _t_{i:02d}_{j}\n\n")
w("tests/test_plugins.py", "\n".join(plug))

w("tests/test_slug.py", '''
import unittest

from textkit.slug import slugify


class SlugTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_punctuation(self):
        self.assertEqual(slugify("  Hello, World!  "), "hello-world")

    def test_custom_separator(self):
        self.assertEqual(slugify("a b", sep="_"), "a_b")

    def test_digits(self):
        self.assertEqual(slugify("Release 2.0"), "release-2-0")

    def test_accents(self):
        self.assertEqual(slugify("Crème Brûlée"), "creme-brulee")

    def test_mixed_accents_and_digits(self):
        self.assertEqual(slugify("Déjà Vu 2"), "deja-vu-2")
''')
w("tests/test_wrap.py", '''
import unittest

from textkit.wrap import wrap_text


class WrapTest(unittest.TestCase):
    def test_short(self):
        self.assertEqual(wrap_text("a b c", 10), ["a b c"])

    def test_break(self):
        self.assertEqual(wrap_text("aaa bbb ccc", 8), ["aaa bbb", "ccc"])

    def test_exact_fit(self):
        self.assertEqual(wrap_text("aaaa bbbb cc", 9), ["aaaa bbbb", "cc"])

    def test_long_word(self):
        self.assertEqual(wrap_text("abcdefghij k", 4), ["abcdefghij", "k"])

    def test_empty(self):
        self.assertEqual(wrap_text("", 5), [])
''')
w("tests/test_case.py", '''
import unittest

from textkit.case import camel, snake


class CaseTest(unittest.TestCase):
    def test_snake(self):
        self.assertEqual(snake("HelloWorld"), "hello_world")
        self.assertEqual(snake("already-kebab case"), "already_kebab_case")

    def test_camel(self):
        self.assertEqual(camel("hello_world"), "helloWorld")
''')
w("tests/test_stats.py", '''
import unittest

from textkit.stats import top_words, word_count


class StatsTest(unittest.TestCase):
    def test_count(self):
        self.assertEqual(word_count("a b  c"), 3)

    def test_top(self):
        self.assertEqual(top_words("b a b c a b", 2), ["b", "a"])
''')
PYEOF

fixture_commit "textkit fixture"
echo "$DIR"
