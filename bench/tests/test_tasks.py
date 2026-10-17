"""Task fixtures: setup.sh is deterministic, check.sh fails on the pristine fixture and passes after a
reference solution (so a bench failure means the agent failed, not the grader)."""
import os
import re
import shutil
import subprocess

import pytest

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(BENCH, "tasks")

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("make")), reason="needs git and make")


def setup(task, dest):
    p = subprocess.run(["sh", os.path.join(TASKS, task, "setup.sh"), str(dest)], capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip().endswith(os.path.basename(str(dest)))
    return dest


def check(task, fixture, answer_text=""):
    answer = fixture.parent / (fixture.name + ".answer.txt")
    answer.write_text(answer_text)
    p = subprocess.run(["sh", os.path.join(TASKS, task, "check.sh"), str(fixture), str(answer)],
                       capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr


def edit(path, old, new):
    text = path.read_text()
    assert old in text, (path, old)
    path.write_text(text.replace(old, new))


def head(fixture):
    return subprocess.run(["git", "-C", str(fixture), "rev-parse", "HEAD"], capture_output=True, text=True).stdout


# ---------------------------------------------------------------- reference solutions

def solve_failing_test(f):
    edit(f / "ledger/money.py",
         "q, r = divmod(numerator, denominator)\n    if r * 2 > denominator:\n        q += 1\n    return q",
         "q, r = divmod(abs(numerator), denominator)\n    if r * 2 >= denominator:\n        q += 1\n"
         "    return -q if numerator < 0 else q")
    return ""


def solve_noisy_suite(f):
    edit(f / "textkit/slug.py", "import re\n", "import re\nimport unicodedata\n")
    edit(f / "textkit/slug.py", "    text = text.lower()\n",
         '    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()\n')
    edit(f / "textkit/wrap.py", "len(candidate) < width", "len(candidate) <= width")
    return ""


def solve_repo_qa(f):
    return ("1. 7 attempts: MAX_DELIVERY_ATTEMPTS in relay/workers/webhook_retry.py, overridden by "
            "RELAY_WEBHOOK_MAX_ATTEMPTS (relay/config/loader.py).\n"
            "2. compute_backoff in relay/workers/backoff.py, capped at 900 seconds.\n"
            "3. MemoryBlobStore (relay/storage/memory.py).\n")


def solve_rename(f):
    for root, dirs, files in os.walk(f):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            p = os.path.join(root, name)
            text = open(p).read()
            new = re.sub(r"\bcalc_total\b", "compute_order_total", text)
            if new != text:
                open(p, "w").write(new)
    return ""


def solve_three_tasks(f):
    edit(f / "tools/csvstat.py", "rows = list(csv.reader(f))", "rows = list(csv.reader(f, delimiter=delimiter))")
    edit(f / "tools/csvstat.py", "stats(args.file)", "stats(args.file, args.delimiter)")
    edit(f / "tools/greet.py", "import argparse\n", "import argparse\nimport os\n")
    edit(f / "tools/greet.py", '    ap.add_argument("name")\n',
         '    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n'
         '    with open(os.path.join(root, "VERSION")) as fh:\n'
         '        ap.add_argument("--version", action="version", version="greet " + fh.read().strip())\n'
         '    ap.add_argument("name")\n')
    return "tools/csvstat.py\ntools/greet.py\nscripts/migrate.py\n"


SOLUTIONS = {
    "failing-test": solve_failing_test,
    "noisy-suite": solve_noisy_suite,
    "repo-qa": solve_repo_qa,
    "rename": solve_rename,
    "three-tasks": solve_three_tasks,
}


def test_every_task_has_a_reference_solution():
    tasks = sorted(d for d in os.listdir(TASKS) if os.path.isfile(os.path.join(TASKS, d, "task.md")))
    assert tasks == sorted(SOLUTIONS)
    for t in tasks:
        for name in ("task.md", "setup.sh", "check.sh", "max_turns"):
            assert os.path.isfile(os.path.join(TASKS, t, name)), (t, name)
        assert int(open(os.path.join(TASKS, t, "max_turns")).read()) > 0


@pytest.mark.parametrize("task", sorted(SOLUTIONS))
def test_pristine_fails_reference_passes(task, tmp_path):
    fixture = setup(task, tmp_path / "repo")
    assert subprocess.run(["git", "-C", str(fixture), "status", "--porcelain"], capture_output=True,
                          text=True).stdout == ""
    code, out = check(task, fixture, "")
    assert code != 0, f"{task}: check passed on the unsolved fixture\n{out}"
    answer = SOLUTIONS[task](fixture)
    code, out = check(task, fixture, answer)
    assert code == 0, f"{task}: reference solution rejected\n{out}"


def test_partial_or_wrong_solutions_fail(tmp_path):
    f = setup("failing-test", tmp_path / "ft")
    edit(f / "ledger/money.py", "if r * 2 > denominator", "if r * 2 >= denominator")  # naive: breaks negatives
    assert check("failing-test", f)[0] != 0
    f = setup("failing-test", tmp_path / "ft2")
    solve_failing_test(f)
    edit(f / "tests/test_invoice.py", "2488", "2488 ")  # touching tests is disallowed
    assert check("failing-test", f)[0] != 0
    f = setup("repo-qa", tmp_path / "qa")
    assert check("repo-qa", f, "Deliveries retry 5 times (WEBHOOK_RETRIES); InMemoryBlobStore")[0] != 0
    f = setup("rename", tmp_path / "rn")
    for root, dirs, files in os.walk(f):  # over-eager rename also hits calc_total_weight / recalc_totals
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            p = os.path.join(root, name)
            text = open(p).read()
            open(p, "w").write(text.replace("calc_total", "compute_order_total"))
    assert check("rename", f)[0] != 0
    f = setup("three-tasks", tmp_path / "tt")
    solve_three_tasks(f)
    assert check("three-tasks", f, "tools/csvstat.py tools/greet.py scripts/migrate.py lib/server.py")[0] != 0


def test_setup_is_deterministic_and_refuses_non_empty_dirs(tmp_path):
    a = setup("repo-qa", tmp_path / "a")
    b = setup("repo-qa", tmp_path / "b")
    assert head(a) and head(a) == head(b)  # same tree, pinned identity and dates -> same commit
    p = subprocess.run(["sh", os.path.join(TASKS, "repo-qa", "setup.sh"), str(a)], capture_output=True, text=True)
    assert p.returncode != 0 and "not empty" in p.stderr


def test_noisy_suite_is_noisy(tmp_path):
    f = setup("noisy-suite", tmp_path / "ns")
    loud = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "CI", "PYTEST_ADDOPTS")}  # hetz quiet env sets these
    p = subprocess.run(["make", "test"], cwd=f, capture_output=True, text=True, timeout=120, env=loud)
    out = p.stdout + p.stderr
    assert p.returncode != 0
    assert len(out) > 30000  # above the default 30k Bash cap and the profile's 12k
    assert out.count("\x1b[") > 100  # colour codes unless NO_COLOR
    assert len(re.findall(r"^FAIL: ", out, re.M)) == 3
    quiet = subprocess.run(["make", "test"], cwd=f, capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, NO_COLOR="1"))
    assert "\x1b[" not in quiet.stdout + quiet.stderr
