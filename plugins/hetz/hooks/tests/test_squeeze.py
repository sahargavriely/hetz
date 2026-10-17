"""Tests for hooks/squeeze.py (PostToolUse Bash output filter). Self-contained: no conftest, temp dirs only."""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time

import pytest

HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(HOOKS, "tests", "fixtures", "squeeze")
sys.path.insert(0, HOOKS)
import squeeze  # noqa: E402

CASES = json.load(open(os.path.join(FIX, "cases.json"), encoding="utf-8"))
SQUEEZED = sorted(k for k, v in CASES.items() if v["expect"] == "squeeze")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def fixture(name):
    with open(os.path.join(FIX, name + ".out"), encoding="utf-8") as f:
        return f.read()


def clean(line):
    """Independent re-implementation of what the model would read: no ANSI, last \\r segment."""
    line = ANSI.sub("", line)
    parts = [p for p in line.split("\r") if p]
    return parts[-1] if parts else ""


def keep_lines(text):
    return [c for c in (clean(ln) for ln in text.split("\n")) if squeeze.KEEP_RE.search(c)]


def ledger_keep_lost(text_in, text_out):
    """Same rule as the replay gate (scripts/hetz_ledger.py keep_lost): raw lines (str.splitlines) that KEEP_RE
    matches must appear in the output after ANSI removal and whitespace strip."""
    norm = lambda line: ANSI.sub("", line).strip()  # noqa: E731
    out = {norm(line) for line in text_out.splitlines()}
    return [line for line in text_in.splitlines() if squeeze.KEEP_RE.search(line) and norm(line) not in out]


def real_ledger_keep_lost():
    """scripts/hetz_ledger.py keep_lost when that script is present (other component), else None."""
    path = os.path.join(HOOKS, "..", "..", "..", "scripts", "hetz_ledger.py")
    try:
        spec = importlib.util.spec_from_file_location("hetz_ledger_for_squeeze_tests", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.keep_lost
    except Exception:
        return None


def payload(command, stdout, stderr="", **resp):
    tool_response = {"stdout": stdout, "stderr": stderr, "interrupted": False, "isImage": False,
                     "noOutputExpected": False}
    tool_response.update(resp)
    return {"session_id": "sess-1234", "transcript_path": "/nonexistent.jsonl", "cwd": "/tmp",
            "permission_mode": "default", "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": command, "description": "x"}, "tool_response": tool_response,
            "tool_use_id": "toolu_01ABC", "duration_ms": 1234}


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in ("HETZ_SQUEEZE", "HETZ_GUARD"):
        monkeypatch.delenv(k, raising=False)
    return data


def run_main(monkeypatch, capsys, obj):
    monkeypatch.setattr(sys, "stdin", io.StringIO(obj if isinstance(obj, str) else json.dumps(obj)))
    squeeze.main()
    out = capsys.readouterr().out
    return json.loads(out) if out else None


def run_hook(stdin, data_dir, extra_env=None):
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(data_dir), HOME=str(data_dir))
    env.pop("HETZ_SQUEEZE", None)
    env.update(extra_env or {})
    return subprocess.run(["sh", os.path.join(HOOKS, "run.sh"), "squeeze.py"], input=stdin,
                          capture_output=True, env=env, timeout=10)


# ---------------------------------------------------------------- fixtures: squeeze + KEEP safety
@pytest.mark.parametrize("name", SQUEEZED)
def test_fixture_is_squeezed(name):
    case, text = CASES[name], fixture(name)
    res = squeeze.squeeze(case["command"], text, "")
    assert res is not None, name
    assert set(res) == {"stdout", "stderr", "lines_in", "lines_out", "chars_in", "chars_out", "tip"}
    assert res["chars_in"] == len(text) and res["chars_out"] == len(res["stdout"]) + len(res["stderr"])
    assert 1 - res["chars_out"] / res["chars_in"] >= case["min_saving"], (name, res["chars_out"], res["chars_in"])
    assert res["lines_out"] < res["lines_in"]


@pytest.mark.parametrize("name", SQUEEZED)
def test_no_keep_line_dropped(name):
    text = fixture(name)
    res = squeeze.squeeze(CASES[name]["command"], text, "")
    kept = keep_lines(text)
    assert kept, "fixture must contain KEEP lines"
    for line in kept:
        assert line in res["stdout"], (name, line)


@pytest.mark.parametrize("name", sorted(CASES))
def test_fast_keep_matches_keep_re(name):
    for line in fixture(name).split("\n"):
        c = clean(line)
        assert squeeze._is_keep(c) == bool(squeeze.KEEP_RE.search(c)), c


def test_keep_line_with_context_survives_stage2():
    lines = ["   Compiling crate-%d v1.0.%d" % (i, i) for i in range(400)]
    lines[200] = "note: before the problem"
    lines[201] = "error[E0308]: mismatched types"
    lines[202] = "  --> src/lib.rs:7:5"
    text = "\n".join(["setup line %s" % chr(65 + i % 26) * (i % 5 + 1) for i in range(150)] + lines)
    res = squeeze.squeeze("cargo build", text, "")
    for needle in lines[200:203]:
        assert needle in res["stdout"]


def test_keep_regex_semantics():
    keep = ["error: boom", "FAILED tests/x.py::t", "npm ERR! code E404", "Traceback (most recent call last):",
            "warning: unused variable `x`", "  ✕ adds (3 ms)", "permission denied", "exit code 2",
            "warning: use of deprecated function"]
    benign = ["tests/test_api.py::test_error_shape PASSED [ 10%]", " PASS  src/errors.test.js (2 s)",
              "    ✓ shows error banner (4 ms)", "npm warn deprecated inflight@1.0.6: leaks memory",
              "   Compiling thiserror v1.0.64", "--- PASS: TestErrorPath (0.00s)", "Finished release"]
    assert all(squeeze.KEEP_RE.search(x) for x in keep)
    assert not any(squeeze.KEEP_RE.search(x) for x in benign)


def test_stage1_rules():
    noise = "\n".join(["npm warn deprecated pkg%d@1.0.0: old" % i for i in range(40)])
    text = ("\x1b[32mgreen\x1b[0m line\n" + "dl 10%\rdl 50%\rdl 100% done\n" + "⠋ resolving\n"
            + "same line\n" * 5 + noise + "\n"
            + "\n".join("Step %d of 50 at 0x%07x" % (i, i * 977) for i in range(50)) + "\nall done")
    res = squeeze.squeeze("npm install", text, "")
    out = res["stdout"].split("\n")
    assert out[0] == "green line" and "dl 100% done" in out and "⠋ resolving" not in out
    assert "same line (×5)" in out
    assert not any("deprecated" in x for x in out)
    assert "… 48 similar lines" in out and out[-1] == "all done"


def _repeated_warning_case():
    return "\n".join(["   Compiling crate-%d v1.0.%d" % (i, i) for i in range(80)]
                     + ["warning: unused import: `std::fmt`"] * 3 + ["    Finished dev [unoptimized] target(s) in 3.2s"])


def _overwrite_and_color_case():
    return "\n".join(["   Compiling crate-%d v1.0.%d" % (i, i) for i in range(120)]
                     + ["error: lock poisoned\rDownloading 100%", "dl 10%\rwarning: retrying\rdl 100% done",
                        "\x1b[42m\x1b[30m PASS \x1b[39m\x1b[49m src/error.test.js (2 s)",
                        "\x1b[31merror\x1b[0m: colored failure", "    Finished dev target(s) in 3.2s"])


@pytest.mark.parametrize("name", SQUEEZED + ["repeated_warning", "overwrite_and_color"])
def test_replay_gate_keep_lost_is_zero(name):
    if name == "repeated_warning":
        command, text = "cargo build", _repeated_warning_case()
    elif name == "overwrite_and_color":
        command, text = "cargo build", _overwrite_and_color_case()
    else:
        command, text = CASES[name]["command"], fixture(name)
    res = squeeze.squeeze(command, text, "")
    assert res is not None
    assert ledger_keep_lost(text, res["stdout"]) == []
    real = real_ledger_keep_lost()
    if real is not None:
        assert real(squeeze.KEEP_RE, text, res["stdout"]) == 0


def test_protected_identical_run_keeps_exact_text():
    out = squeeze.squeeze("cargo build", _repeated_warning_case(), "")["stdout"].split("\n")
    i = out.index("warning: unused import: `std::fmt`")
    assert out[i + 1] == "… repeated 2 more times"
    assert not any("(×" in line for line in out)


def test_overwritten_keep_segment_survives():
    out = squeeze.squeeze("cargo build", _overwrite_and_color_case(), "")["stdout"].split("\n")
    for line in ("error: lock poisoned", "Downloading 100%", "warning: retrying", "dl 100% done",
                 "error: colored failure"):
        assert line in out, line
    assert "dl 10%" not in out


def test_prefix_sharing_distinct_lines_are_content():
    rows = ["src/acme/service/module_%s.py    120    4   97%%   12-15" % w
            for w in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa")]
    text = "\n".join(["tests/test_mod.py::test_case_%d PASSED [ %d%%]" % (i, i) for i in range(100)]
                     + ["---------- coverage: platform darwin ----------"] + rows + ["TOTAL 1200 40 97%"])
    res = squeeze.squeeze("pytest -v --cov=src", text, "")
    assert res is not None
    for row in rows:
        assert row in res["stdout"].split("\n")
    assert "similar lines" not in res["stdout"]


def test_lines_equal_up_to_digits_still_collapse():
    text = "\n".join("Step %d of 90 at 0x%07x" % (i, i * 977) for i in range(90)) + "\nall done"
    out = squeeze.squeeze("make", text, "")["stdout"].split("\n")
    assert out == ["Step 0 of 90 at 0x0000000", "Step 1 of 90 at 0x00003d1", "… 88 similar lines", "all done"]


@pytest.mark.parametrize("line", [
    "a::b" * 25000 + " error",                                   # pytest-id benign alternative
    "x::y " + "(" * 100000 + " error",
    "tests/a.py::t " + " " * 100000 + "PASSED error",
    "warning " + "a@b:" * 25000 + " deprecated",                 # yarn deprecation alternative
    "warning " + "@:" * 50000,
    "warning " + "a > " * 25000 + "x@1: deprecated",
])
def test_regexes_are_linear_on_long_lines(line):
    t = time.perf_counter()
    squeeze._is_keep(line)
    squeeze.KEEP_RE.search(line)
    squeeze._is_noise(line)
    squeeze._clean_lines(line)
    assert (time.perf_counter() - t) < 0.15


@pytest.mark.parametrize("line", [
    "tests/test_api.py::test_error_shape PASSED [ 10%]",
    "warning request@2.88.2: request has been deprecated, use fetch",
    "warning jest > jest-cli > request@2.88.2: request has been deprecated",
    "warning package.json: No license field",
    "tests/test_api.py::test_error_" + "x" * 360 + " PASSED",
    "tests/test_api.py::test_error_" + "x" * 380 + " PASSED",
    "tests/test_api.py::test_error_" + "x" * 400 + " PASSED",
])
def test_fast_keep_matches_keep_re_at_length_bound(line):
    assert squeeze._is_keep(line) == bool(squeeze.KEEP_RE.search(line))
    if len(line) > squeeze.LONG_LINE:
        assert squeeze._is_keep(line)        # long lines skip the benign exclusion: they stay KEEP


def test_yarn_transitive_deprecations_are_noise():
    lines = ["warning a%d > b > req%d@2.0.%d: request has been deprecated" % (i, i, i) for i in range(60)]
    res = squeeze.squeeze("yarn install", "\n".join(lines + ["Done in 3.2s."]), "")
    assert res["stdout"] == "Done in 3.2s."


# ---------------------------------------------------------------- passthrough
@pytest.mark.parametrize("command", [
    "git diff", "git diff HEAD~1 -- src", "git -C repo show HEAD", "git log -p -3", "cat build.log",
    "grep -rn error .", "tail -n 500 build.log", "sed -n 1,400p x.txt", "ls -la", "python script.py",
    "make && cat out.txt", "echo start && make", "make # hz:raw", "rtk cargo build", "npm view react",
    "docker compose logs web", "terraform plan", "cargo run --release",
])
def test_passthrough_commands(command):
    assert squeeze.squeeze(command, fixture("cargo_build"), "") is None


def test_git_diff_untouched(env, monkeypatch, capsys):
    text = fixture("git_diff")
    assert squeeze.squeeze("git diff", text, "") is None
    assert run_main(monkeypatch, capsys, payload("git diff", text)) is None


@pytest.mark.parametrize("command,key", [
    ("cd web && npm ci", "npm"), ("FOO=1 npx jest --ci", "jest"), ("python -m pytest -v", "pytest"),
    ("uv run pytest -x", "pytest"), ("npm test 2>&1 | tail -80", "npm"), ("time cargo build --release", "cargo"),
    ("./gradlew build", "gradlew"), (".venv/bin/pytest", "pytest"), ("uv pip install -r req.txt", "uv"),
    ("docker compose build", "docker"), ("timeout 600 make -j8", "make"), ("nvm use 20 && npm install", "npm"),
    ("git commit -m 'x && make'", None), ("make > build.log 2>&1", "make"),
    ("for d in a b; do (cd $d && npm install); done", "npm"), ("if true; then cargo test; fi", "cargo"),
    ("while read l; do make $l; done < targets", None),
])
def test_tool_key(command, key):
    assert squeeze.tool_key(command) == key


@pytest.mark.parametrize("command", [
    "pytest --collect-only -q", "pytest --co", "python -m pytest --fixtures", "pytest --markers",
    "go test -json ./...", "go test -list . ./pkg", "go mod graph", "go mod why -m all", "go build -n ./...",
    "./gradlew dependencies", "./gradlew :app:dependencies --configuration runtimeClasspath", "gradle tasks --all",
    "gradle -m build", "mvn dependency:tree", "mvn -q help:effective-pom", "tsc --listFiles", "tsc --showConfig",
    "npx tsc --traceResolution", "make -n", "make -np", "make --dry-run all", "make -p", "make help", "make logs",
    "make print-CFLAGS", "jest --listTests", "npx jest --showConfig", "eslint --print-config x.js",
    "eslint -f json src", "eslint --format=checkstyle .", "cargo test -- --list",
    "cargo build --message-format=json", "npm run", "npm run routes", "yarn run logs:web", "npm test -- --json",
    "npm install --dry-run", "vitest list", "vitest run --reporter=json", "cmake -LAH .", "cmake -E capabilities",
    "ninja -t targets", "pip install --dry-run --report - x", "docker build --progress=rawjson .",
    "apt-get install -s curl", "pytest --help", "cargo build -h",
])
def test_listing_and_structured_calls_pass_through(command):
    assert squeeze.tool_key(command) is None
    assert squeeze.squeeze(command, fixture("cargo_build"), "") is None


@pytest.mark.parametrize("command,key", [
    ("make -j8", "make"), ("make -s", "make"), ("make -C sub all", "make"), ("make -Oline", "make"),
    ("pytest -n 4", "pytest"), ("pytest -p no:cacheprovider -q", "pytest"), ("go mod tidy", "go"),
    ("npm run build", "npm"), ("mvn -B package", "mvn"), ("./gradlew build --console=plain", "gradlew"),
    ("docker build --progress=plain .", "docker"), ("vitest run --reporter=dot", "vitest"),
    ("go test ./...", "go"), ("tsc --noEmit", "tsc"), ("jest --ci", "jest"), ("make deps", "make"),
])
def test_noisy_calls_still_squeezed(command, key):
    assert squeeze.tool_key(command) == key


def test_small_output_and_low_saving_pass_through():
    assert squeeze.squeeze("npm install", "added 3 packages in 1s\n" * 50, "") is None   # < 2000 chars
    distinct = "\n".join("line %s unique content %s" % (chr(65 + i % 26), "xyz"[i % 3] * (i % 40)) for i in range(70))
    assert len(distinct) > 2000 and squeeze.squeeze("make", distinct, "") is None      # nothing to drop, < 80 lines


@pytest.mark.parametrize("flags", [{"interrupted": True}, {"isImage": True}, {"backgroundTaskId": "b1"},
                                   {"structuredContent": [{"type": "text", "text": "x"}]}])
def test_response_flags_pass_through(env, monkeypatch, capsys, flags):
    assert run_main(monkeypatch, capsys, payload("cargo build", fixture("cargo_build"), **flags)) is None


def test_switch_off(env, monkeypatch, capsys):
    monkeypatch.setenv("HETZ_SQUEEZE", "off")
    assert run_main(monkeypatch, capsys, payload("cargo build", fixture("cargo_build"))) is None
    assert not (env / "squeeze.tsv").exists()


# ---------------------------------------------------------------- hook output shape + side effects
def test_output_shape_and_side_effects(env, monkeypatch, capsys):
    text = fixture("npm_install")
    inp = payload("npm install", text)
    out = run_main(monkeypatch, capsys, inp)
    assert set(out) == {"hookSpecificOutput"}
    hso = out["hookSpecificOutput"]
    assert set(hso) == {"hookEventName", "updatedToolOutput"} and hso["hookEventName"] == "PostToolUse"
    upd, orig = hso["updatedToolOutput"], inp["tool_response"]
    assert set(upd) == set(orig)
    assert all(type(upd[k]) is type(orig[k]) for k in orig)
    assert {k: v for k, v in upd.items() if k not in ("stdout", "stderr")} == \
           {k: v for k, v in orig.items() if k not in ("stdout", "stderr")}
    assert upd["stderr"] == ""
    footer = upd["stdout"].rsplit("\n", 1)[-1]
    m = re.fullmatch(r"\[hetz squeeze (\d+)→(\d+) lines · raw: (\S+) \(grep it\) · quieter: (.+)\]", footer)
    assert m, footer
    log = m.group(3)
    assert log == str(env / "logs" / "sess-1234" / "toolu_01ABC.log")
    raw = open(log, encoding="utf-8").read()
    assert raw.startswith("$ npm install\n") and text in raw
    assert "--loglevel=error" in m.group(4) and "--silent" not in m.group(4)
    rows = (env / "squeeze.tsv").read_text().splitlines()
    assert len(rows) == 1
    cols = rows[0].split("\t")
    assert len(cols) == 5 and cols[1] == "sess-1234" and cols[2] == "npm install"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", cols[0])
    assert int(cols[3]) == len(text) and int(cols[4]) == len(upd["stdout"])


def test_large_stderr_squeezed_small_stderr_verbatim():
    note = "Shell cwd was reset to /repo"
    res = squeeze.squeeze("cargo build", fixture("cargo_build"), note)
    assert res["stderr"] == note
    res = squeeze.squeeze("cargo build", "Finished\n" * 3, fixture("cargo_build"))
    assert res is not None and "warning: unused variable: `retries`" in res["stderr"]


def test_tip_suppressed_when_already_quiet():
    assert squeeze.tip_for("cargo", "cargo build") == "cargo -q"
    assert squeeze.tip_for("cargo", "cargo build -q") == ""
    assert "hides failure detail" in squeeze.tip_for("gradle", "gradle build")
    assert "tail -40" in squeeze.tip_for("no-such-tool", "x")


def test_tips_by_subcommand():
    assert squeeze.tip_for("go", "go test -v ./...", "test").startswith("go test ./...")
    assert squeeze.tip_for("go", "go test ./...", "test") == ""          # tip without flags, already followed
    assert "tail -40" in squeeze.tip_for("go", "go build ./...", "build")     # no misleading `go test` tip
    assert squeeze.tip_for("docker", "docker pull nginx", "pull") == "docker pull -q"
    assert squeeze.tip_for("docker", "docker push x/y", "push") == "docker push -q"
    assert "build --progress=plain" in squeeze.tip_for("docker", "docker build .", "build")
    pull = "\n".join("%012x: Pull complete" % (i * 7919) for i in range(80)) + "\nStatus: Downloaded newer image"
    assert squeeze.squeeze("docker pull nginx", pull, "")["tip"] == "docker pull -q"


def test_no_tip_teaches_error_hiding_without_a_caveat():
    tips = squeeze._load_tips()
    assert "--silent" not in tips["npm"][0] and "reporter=silent" not in tips["pnpm"][0]
    for key, (tip, note) in tips.items():          # flags known to hide failure detail must carry a caveat
        if re.search(r"--silent\b|reporter=silent|--loglevel[= ]silent", tip) or key in ("gradle", "gradlew",
                                                                                          "composer"):
            assert note, key


def test_persisted_over_cap_output_is_inlined(env, monkeypatch, capsys, tmp_path):
    full = "\n".join("Compiling crate-%d v1.0.%d" % (i, i) for i in range(1, 1500))
    full = full.replace("Compiling crate-750 v1.0.750", "Compiling crate-750 v1.0.750\nwarning: unused variable x")
    path = tmp_path / "toolu_01ABC.txt"
    path.write_text(full)
    inp = payload("make", full[:30000], persistedOutputPath=str(path), persistedOutputSize=len(full))
    upd = run_main(monkeypatch, capsys, inp)["hookSpecificOutput"]["updatedToolOutput"]
    assert "persistedOutputPath" not in upd and "persistedOutputSize" not in upd
    assert "warning: unused variable x" in upd["stdout"] and len(upd["stdout"]) < 1000
    assert open(str(env / "logs" / "sess-1234" / "toolu_01ABC.log")).read().count("\n") >= 1500
    # unreadable persisted file -> pass through
    inp["tool_response"]["persistedOutputPath"] = str(tmp_path / "missing.txt")
    assert run_main(monkeypatch, capsys, inp) is None


def test_persisted_inline_never_exceeds_preview(env, monkeypatch, capsys, tmp_path):
    words = ["%s%s" % (a, b) for a in "bcdfghjklm" for b in ("ark", "elt", "ond", "usk", "imp", "oat")]
    full = "\n".join(["Compiling crate-%d v1.0.%d" % (i, i) for i in range(1500)]
                     + ["linking target %s with profile release" % w for w in words] + ["Finished in 9s"])
    assert squeeze.squeeze("make", full, "")["chars_out"] > squeeze.PREVIEW_CHARS     # normal stage 2 too big
    path = tmp_path / "toolu_01ABC.txt"
    path.write_text(full)
    inp = payload("make", full[:30000], persistedOutputPath=str(path), persistedOutputSize=len(full))
    upd = run_main(monkeypatch, capsys, inp)["hookSpecificOutput"]["updatedToolOutput"]
    assert len(upd["stdout"]) + len(upd["stderr"]) <= squeeze.PREVIEW_CHARS
    assert "Finished in 9s" in upd["stdout"] and "lines omitted" in upd["stdout"]
    cols = (env / "squeeze.tsv").read_text().splitlines()[-1].split("\t")
    assert int(cols[4]) <= int(cols[3]) == squeeze.PREVIEW_CHARS


def test_persisted_too_big_after_squeeze_passes_through(env, monkeypatch, capsys, tmp_path):
    full = "\n".join("warning: distinct problem number %d in module_%d" % (i, i) for i in range(2000))
    path = tmp_path / "big.txt"
    path.write_text(full)
    inp = payload("make", full[:30000], persistedOutputPath=str(path), persistedOutputSize=len(full))
    assert run_main(monkeypatch, capsys, inp) is None


# ---------------------------------------------------------------- commit flag
@pytest.mark.parametrize("command,stdout,flag", [
    ("git commit -m 'feat: x'", "[main 1a2b3c4] feat: x\n 1 file changed", True),
    ("git add -A && git commit -qm wip", "", True),
    ("git -C sub commit -m x; echo done", "[dev (root-commit) 9f8e7d6] x\ndone", True),
    ("git commit -m x; echo done", "done", False),
    ("git commit -m x 2>&1 | tail -5", "error: pre-commit hook failed (add --no-verify to bypass)", False),
    ("git commit -m x | tail -3", "[main 1a2b3c4] x\n 1 file changed", True),
    ("git commit -m x || true", "nothing to commit, working tree clean", False),
    ("git commit --dry-run -m x", "On branch main", False),
    ("echo 'git commit'", "git commit", False),
    ("git status", "nothing to commit", False),
])
def test_commit_flag(env, monkeypatch, capsys, command, stdout, flag):
    assert run_main(monkeypatch, capsys, payload(command, stdout)) is None
    assert (env / "state" / "sess-1234.commit").exists() is flag


def test_commit_flag_respects_guard_switch_and_not_squeeze_switch(env, monkeypatch, capsys):
    monkeypatch.setenv("HETZ_SQUEEZE", "0")
    run_main(monkeypatch, capsys, payload("git commit -m x", "[main 1a2b3c4] x"))
    assert (env / "state" / "sess-1234.commit").exists()
    os.remove(str(env / "state" / "sess-1234.commit"))
    monkeypatch.setenv("HETZ_GUARD", "no")
    run_main(monkeypatch, capsys, payload("git commit -m x", "[main 1a2b3c4] x"))
    assert not (env / "state" / "sess-1234.commit").exists()


# ---------------------------------------------------------------- fail open
@pytest.mark.parametrize("stdin", [
    b"", b"not json", b"[]", b"null", b'{"tool_name": "Bash"}', b'{"tool_name":"Bash","tool_response":5}',
    b'{"tool_name":"Bash","tool_input":{"command":"make"},"tool_response":{"stdout":7,"stderr":null}}',
    b'{"tool_name":"Bash","tool_input":{"command":["make"]},"tool_response":{"stdout":"x"}}',
    b"\xff\xfe\x00garbage", b'{"tool_name":"Read","tool_input":{},"tool_response":{}}',
])
def test_fail_open_on_garbage_stdin(tmp_path, stdin):
    r = run_hook(stdin, tmp_path)
    assert r.returncode == 0 and r.stdout == b""


def test_fail_open_when_data_dir_unwritable(tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    inp = json.dumps(payload("cargo build", fixture("cargo_build"))).encode()
    r = run_hook(inp, blocker)
    assert r.returncode == 0 and r.stdout == b""


def test_hook_process_emits_valid_json(tmp_path):
    inp = json.dumps(payload("docker build .", fixture("docker_build"))).encode()
    r = run_hook(inp, tmp_path)
    assert r.returncode == 0
    upd = json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]
    assert "[hetz squeeze" in upd["stdout"] and upd["interrupted"] is False


# ---------------------------------------------------------------- latency
def p95(samples):
    samples = sorted(samples)
    return samples[max(0, int(round(0.95 * len(samples))) - 1)]


def test_p95_latency_in_process(env, monkeypatch, capsys):
    samples = []
    for _ in range(20):
        for name in sorted(CASES):
            inp = json.dumps(payload(CASES[name]["command"], fixture(name)))
            monkeypatch.setattr(sys, "stdin", io.StringIO(inp))
            t = time.perf_counter()
            squeeze.main()
            samples.append((time.perf_counter() - t) * 1000)
            capsys.readouterr()
    assert p95(samples) < 100, p95(samples)


def test_p95_latency_hook_process(tmp_path):
    inp = json.dumps(payload("npx jest --verbose", fixture("jest_fail"))).encode()
    samples = []
    for _ in range(12):
        t = time.perf_counter()
        r = run_hook(inp, tmp_path)
        samples.append((time.perf_counter() - t) * 1000)
        assert r.stdout
    assert p95(samples) < 100, samples
