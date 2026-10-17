"""run.py: arm resolution, scheduling, --dry-run output, and --execute against a fake claude (no tokens)."""
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import textwrap

import pytest

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(BENCH)
RUN = os.path.join(BENCH, "run.py")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bench = _load("hetz_bench_run", RUN)
SPECS = bench.load_arm_specs()


def arm(name):
    return bench.resolve_arm(name, SPECS)


def dry(*args, env=None):
    return subprocess.run([sys.executable, RUN, *args], capture_output=True, text=True, env=env)


# ---------------------------------------------------------------- arms and tasks

def test_design_arms_present_and_resolvable():
    want = {"A", "B", "C", "D", "D-sonnet", "D-no-cap", "D-no-squeeze", "D-no-contract", "D-no-crew",
            "D-no-explore", "D-no-quietenv", "D-clear", "D-handoff", "D-delegate"}
    assert want <= set(SPECS)
    for name in SPECS:
        a = arm(name)
        json.dumps(a["settings"])
        for d in a["plugin_dirs"]:
            assert os.path.isdir(os.path.join(REPO, d)), d


def test_arm_inheritance_chain():
    a, b, c, d = arm("A"), arm("B"), arm("C"), arm("D")
    assert a["settings"] == {"outputStyle": "Concise"} and a["plugin_dirs"] == [] and a["env"] == {}
    assert b["settings"]["outputStyle"] == "Concise"
    assert b["settings"]["autoCompactWindow"] == 200000 and b["settings"]["bashOutputMaxChars"] == 12000
    assert b["plugin_dirs"] == []
    assert c["plugin_dirs"] == ["plugins/hetz"] and c["env"]["HETZ_SQUEEZE"] == "1"
    assert d["plugin_dirs"] == ["plugins/hetz", "plugins/hetz-crew"]
    assert d["project_files"] == {".claude/agents/Explore.md": "plugins/hetz/templates/Explore.md"}
    assert d["settings"] == b["settings"] and d["ablates"] is None


def test_ablations_remove_exactly_one_lever():
    d = arm("D")
    nocap = arm("D-no-cap")
    assert "autoCompactWindow" not in nocap["settings"]
    assert {k: v for k, v in d["settings"].items() if k != "autoCompactWindow"} == nocap["settings"]
    assert arm("D-no-squeeze")["env"]["HETZ_SQUEEZE"] == "0"
    assert arm("D-no-contract")["env"]["HETZ_CONTRACT"] == "0"
    assert arm("D-no-quietenv")["env"]["HETZ_QUIET_ENV"] == "off"
    assert arm("D-no-crew")["plugin_dirs"] == ["plugins/hetz"]
    assert arm("D-no-explore")["project_files"] == {}
    for name in ("D-no-cap", "D-no-squeeze", "D-no-contract", "D-no-crew", "D-no-explore", "D-no-quietenv"):
        a = arm(name)
        assert a["ablates"] and a["ablation_base"] == "D"
        assert a["only_tasks"] is None
    assert arm("D-sonnet")["model"] == "sonnet"
    assert arm("D-handoff")["rotate"] == "handoff" and arm("D-handoff")["only_tasks"] == ["three-tasks"]
    assert arm("D")["only_tasks"] is None  # not inherited downward either


def test_arm_b_tracks_the_real_profile():
    b = arm("B")
    assert "statusLine" not in b["settings"]
    assert b["settings"]["env"]["CLAUDE_CODE_IDLE_THRESHOLD_MINUTES"] == "60"  # placeholder filled
    assert "Read(//**/*.min.js)" in b["settings"]["permissions"]["deny"]
    assert not re.search(r"\{[a-z_]+\}", json.dumps(b["settings"]))


def test_profile_settings_rules(tmp_path, monkeypatch):
    prof = tmp_path / "p.json"
    prof.write_text(json.dumps({"_doc": "x", "items": {
        "one": {"settings": {"env": {"A": "{v}"}, "permissions": {"deny": ["X"]}}},
        "two": {"settings": {"env": {"B": "2"}, "permissions": {"deny": ["X", "Y"]}}},
        "three": {"settings": {"statusLine": {"command": "{cmd}"}}}}}))
    monkeypatch.setattr(bench, "REPO", str(tmp_path))
    got = bench.profile_settings({"path": "p.json", "exclude": ["three"], "fill": {"v": "1"}}, "t")
    assert got == {"env": {"A": "1", "B": "2"}, "permissions": {"deny": ["X", "Y"]}}
    with pytest.raises(bench.BenchError, match="unfilled placeholder"):
        bench.profile_settings({"path": "p.json", "fill": {"v": "1"}}, "t")
    with pytest.raises(bench.BenchError, match="no item"):
        bench.profile_settings({"path": "p.json", "exclude": ["nope"]}, "t")
    with pytest.raises(bench.BenchError, match="cannot read"):
        bench.profile_settings({"path": "missing.json"}, "t")


def test_resolve_errors():
    with pytest.raises(bench.BenchError):
        bench.resolve_arm("nope", SPECS)
    with pytest.raises(bench.BenchError, match="cycle"):
        bench.resolve_arm("x", {"x": {"extends": "y"}, "y": {"extends": "x"}})
    with pytest.raises(bench.BenchError, match="rotate"):
        bench.resolve_arm("x", {"x": {"rotate": "sometimes"}})


def test_deep_merge_and_remove_path():
    merged = bench.deep_merge({"a": {"b": 1, "c": 2}, "l": [1]}, {"a": {"c": None, "d": 3}, "l": [2]})
    assert merged == {"a": {"b": 1, "d": 3}, "l": [2]}
    obj = {"env": {"X": "1", "Y": "2"}}
    bench.remove_path(obj, "env.X")
    bench.remove_path(obj, "missing.key")
    assert obj == {"env": {"Y": "2"}}


def test_tasks_load():
    names = bench.list_tasks()
    assert {"failing-test", "noisy-suite", "repo-qa", "rename", "three-tasks"} <= set(names)
    assert "_lib.sh" not in names
    for name in names:
        t = bench.load_task(name)
        assert t["max_turns"] > 0 and t["turns"]
    assert len(bench.load_task("three-tasks")["turns"]) == 3
    with pytest.raises(bench.BenchError):
        bench.load_task("nope")


# ---------------------------------------------------------------- schedule and invocations

def test_schedule_counts_warmup_and_seed():
    arms = [arm("A"), arm("D"), arm("D-handoff")]
    tasks = [bench.load_task("failing-test"), bench.load_task("three-tasks")]
    s1 = bench.build_schedule(arms, tasks, k=3, warmup=1, seed=42)
    # D-handoff only runs three-tasks: (2 + 2 + 1) cells x 4 runs
    assert len(s1) == 5 * 4
    assert [r["order"] for r in s1] == list(range(20))
    for cell in {(r["arm"], r["task"]) for r in s1}:
        runs = [r for r in s1 if (r["arm"], r["task"]) == cell]
        assert [r["rep"] for r in runs] == [0, 1, 2, 3]
        assert [r["warmup"] for r in runs] == [True, False, False, False]
    s2 = bench.build_schedule(arms, tasks, k=3, warmup=1, seed=42)
    assert s1 == s2
    grouped = sorted(s1, key=lambda r: (r["arm"], r["task"], r["rep"]))
    assert [(r["arm"], r["task"]) for r in s1] != [(r["arm"], r["task"]) for r in grouped]
    assert bench.build_schedule(arms, tasks, 3, 1, 43) != s1


def test_plan_invocations_rotation_modes():
    t3 = bench.load_task("three-tasks")
    resume = bench.plan_invocations(arm("D"), t3)
    assert [(k, m, key) for k, _, _, m, key in resume] == [("turn", "new", 0), ("turn", "resume", 0),
                                                           ("turn", "resume", 0)]
    clear = bench.plan_invocations(arm("D-clear"), t3)
    assert [(k, m, key) for k, _, _, m, key in clear] == [("turn", "new", 0), ("turn", "new", 1),
                                                          ("turn", "new", 2)]
    handoff = bench.plan_invocations(arm("D-handoff"), t3)
    assert [(k, m, key) for k, _, _, m, key in handoff] == [
        ("turn", "new", 0), ("handoff", "resume", 0), ("turn", "new", 1), ("handoff", "resume", 1),
        ("turn", "new", 2)]
    assert handoff[1][2] == "/hetz:handoff"
    single = bench.plan_invocations(arm("D-handoff"), bench.load_task("repo-qa"))
    assert len(single) == 1 and single[0][3] == "new"


def test_claude_argv_flags():
    class O:
        model, effort, budget_usd = "claude-opus-5-5", "medium", 3.0
    t = bench.load_task("repo-qa")
    argv = bench.claude_argv("claude", "PROMPT", arm("D"), t, "/w/settings/D.json", O, ["--session-id", "u"])
    assert argv[:3] == ["claude", "-p", "PROMPT"]
    s = " ".join(argv)
    for flag in ("--output-format json", "--setting-sources project,local", "--settings /w/settings/D.json",
                 "--strict-mcp-config", "--model claude-opus-5-5", "--effort medium",
                 f"--max-turns {t['max_turns']}", "--max-budget-usd 3", "--permission-mode acceptEdits",
                 "--session-id u"):
        assert flag in s, flag
    assert argv.count("--plugin-dir") == 2
    assert os.path.join(REPO, "plugins/hetz-crew") in argv
    sonnet = bench.claude_argv("claude", "P", arm("D-sonnet"), t, "/s", O, [])
    assert sonnet[sonnet.index("--model") + 1] == "sonnet"


def test_clean_env_strips_leaks_and_applies_arm():
    base = {"PATH": "/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "http://proxy",
            "CLAUDECODE": "1", "HETZ_SQUEEZE": "0", "HETZ_ANYTHING": "x", "NO_COLOR": "1", "CLAUDE_EFFORT": "max",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_AGENT_SDK_VERSION": "1",
            "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH": "1", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "9",
            "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDE_CODE_SKIP_VERTEX_AUTH": "1",
            "CLAUDE_CODE_CLIENT_CERT": "c", "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "5",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "x", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "y",
            "ANTHROPIC_DEFAULT_FABLE_MODEL": "z", "ANTHROPIC_CUSTOM_MODEL_OPTION": "w",
            "ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION": "us-east-1"}
    env = bench.clean_env(arm("C"), base)
    for kept in ("PATH", "HOME", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SKIP_VERTEX_AUTH", "CLAUDE_CODE_CLIENT_CERT",
                 "CLAUDE_CODE_API_KEY_HELPER_TTL_MS", "ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION"):
        assert env[kept] == base[kept], kept
    for gone in ("CLAUDECODE", "HETZ_ANYTHING", "NO_COLOR", "CLAUDE_EFFORT", "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
                 "CLAUDE_CODE_SESSION_ID", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
                 "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                 "ANTHROPIC_DEFAULT_FABLE_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION"):
        assert gone not in env, gone
    assert env["HETZ_SQUEEZE"] == "1" and env["DISABLE_AUTOUPDATER"] == "1"
    names = bench.stripped_env_names(base)
    assert "HETZ_SQUEEZE" in names and "CLAUDE_CODE_USE_BEDROCK" not in names


# ---------------------------------------------------------------- --dry-run CLI

def claude_lines(out):
    return [l for l in out.splitlines() if l.startswith("(cd ")]


def test_dry_run_is_default_and_writes_nothing(tmp_path):
    out_dir, work = tmp_path / "results", tmp_path / "work"
    args = ["--arms", "A,D", "--tasks", "failing-test,three-tasks", "-k", "2", "--warmup", "1", "--seed", "11",
            "--out", str(out_dir), "--work-root", str(work)]
    p = dry(*args)
    assert p.returncode == 0, p.stderr
    assert "DRY RUN" in p.stdout
    assert not out_dir.exists() and not work.exists()
    headers = re.findall(r"^# \[(\d+)/(\d+)\] arm=(\S+) task=(\S+) rep=(\d+)( \(warm-up\))?$", p.stdout, re.M)
    assert len(headers) == 2 * 2 * 3
    assert sum(1 for h in headers if h[5]) == 4  # one warm-up per cell
    lines = claude_lines(p.stdout)
    assert len(lines) == 2 * 3 * (1 + 3)  # failing-test: 1 call, three-tasks: 3 calls
    for line in lines:
        for flag in ("claude -p ", "--output-format json", "--setting-sources project,local",
                     f"--settings {work}/settings/", "--model claude-opus-5-5", "--max-budget-usd 3",
                     "--strict-mcp-config", "--effort medium", "DISABLE_AUTOUPDATER=1"):
            assert flag in line, (flag, line)
        assert ("--plugin-dir" in line) == ("-D-" in line)
    ft = [l for l in lines if "-failing-test-" in l]
    assert all("--max-turns 30" in l and '"$(cat ' in l for l in ft)
    d_line = next(l for l in lines if "-D-" in l)
    assert f"--plugin-dir {REPO}/plugins/hetz " in d_line and f"{REPO}/plugins/hetz-crew" in d_line
    assert "HETZ_SQUEEZE=1" in d_line
    assert "cp " in p.stdout and ".claude/agents/Explore.md" in p.stdout
    # repeat with --dry-run explicitly and the same seed: same run order
    p2 = dry("--dry-run", *args)
    assert p2.returncode == 0
    order = lambda o: re.findall(r"^# \[\d+/\d+\] .*$", o, re.M)
    assert order(p.stdout) == order(p2.stdout)


def test_dry_run_multi_turn_sessions(tmp_path):
    p = dry("--arms", "D,D-handoff", "--tasks", "three-tasks", "-k", "1", "--warmup", "0", "--seed", "5",
            "--work-root", str(tmp_path / "w"))
    assert p.returncode == 0, p.stderr
    blocks = [b for b in p.stdout.split("\n\n") if b.startswith("# [")]
    assert len(blocks) == 2
    for block in blocks:
        lines = claude_lines(block)
        sessions = [re.search(r"--(session-id|resume) (" + UUID + ")", l).groups() for l in lines]
        if "arm=D-handoff" in block:
            assert len(lines) == 5
            assert [m for m, _ in sessions] == ["session-id", "resume", "session-id", "resume", "session-id"]
            assert sessions[1][1] == sessions[0][1] and sessions[3][1] == sessions[2][1]
            assert len({sid for _, sid in sessions}) == 3
            assert "-p /hetz:handoff " in lines[1]
        else:
            assert len(lines) == 3
            assert [m for m, _ in sessions] == ["session-id", "resume", "resume"]
            assert len({sid for _, sid in sessions}) == 1
        assert "--max-turns 20" in lines[0]


def test_preset_cheap_and_errors(tmp_path):
    p = dry("--preset", "cheap", "-k", "1", "--warmup", "0", "--seed", "1", "--work-root", str(tmp_path))
    assert p.returncode == 0
    cells = set(re.findall(r"arm=(\S+) task=(\S+)", p.stdout))
    assert cells == {(a, t) for a in ("A", "D") for t in ("repo-qa", "noisy-suite", "three-tasks")}
    bad = dry("--arms", "Z")
    assert bad.returncode == 2 and "unknown arm" in bad.stderr
    both = dry("--dry-run", "--execute")
    assert both.returncode == 2


def test_list():
    p = dry("--list")
    assert p.returncode == 0
    assert "arm  D-no-squeeze" in p.stdout and "task three-tasks" in p.stdout


# ---------------------------------------------------------------- --execute with a fake claude

FAKE_CLAUDE = r'''
#!PYTHON
"""Stand-in for `claude`: logs argv/env, fixes the failing-test fixture, prints a result JSON."""
import json, os, sys, uuid
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.272 (Claude Code)")
    sys.exit(0)
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": args, "cwd": os.getcwd(),
                        "env": {k: os.environ.get(k) for k in ("HETZ_SQUEEZE", "CLAUDECODE", "DISABLE_AUTOUPDATER")}})
            + "\n")
sid = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
money = "ledger/money.py"
if os.path.exists(money):
    s = open(money).read()
    s = s.replace("q, r = divmod(numerator, denominator)\n    if r * 2 > denominator:\n        q += 1\n    return q",
                  "q, r = divmod(abs(numerator), denominator)\n    if r * 2 >= denominator:\n        q += 1\n"
                  "    return -q if numerator < 0 else q")
    open(money, "w").write(s)
proj = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "fake")
os.makedirs(proj, exist_ok=True)
with open(os.path.join(proj, sid + ".jsonl"), "a") as f:
    for i, (cr, cc) in enumerate([(0, 30000), (30000, 2000), (32000, 25000)]):
        f.write(json.dumps({"type": "assistant", "message": {"id": f"m{i}", "role": "assistant", "usage": {
            "input_tokens": 10, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc,
            "output_tokens": 50}}}) + "\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
                  "duration_ms": 1000, "total_cost_usd": 0.05, "session_id": sid, "result": "fixed it",
                  "usage": {"input_tokens": 30, "cache_read_input_tokens": 62000,
                            "cache_creation_input_tokens": 57000, "output_tokens": 150},
                  "modelUsage": {"claude-opus-5-5": {"inputTokens": 30, "outputTokens": 150, "costUSD": 0.05}}}))
'''


def test_execute_with_fake_claude(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text(textwrap.dedent(FAKE_CLAUDE).lstrip().replace("PYTHON", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log, out_dir, work, cfg = tmp_path / "log.jsonl", tmp_path / "results", tmp_path / "hetz-bench", tmp_path / "cfg"
    env = dict(os.environ, FAKE_LOG=str(log), CLAUDE_CONFIG_DIR=str(cfg), CLAUDECODE="1", HETZ_SQUEEZE="0")
    p = dry("--execute", "--claude", str(fake), "--arms", "A,C", "--tasks", "failing-test", "-k", "1",
            "--warmup", "1", "--seed", "9", "--out", str(out_dir), "--work-root", str(work), "--keep", "none",
            env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    (results,) = list(out_dir.glob("*.json"))
    doc = json.loads(results.read_text())
    assert doc["meta"]["claude_version"] == "2.1.272" and doc["meta"]["seed"] == 9
    assert "CLAUDECODE" in doc["meta"]["stripped_env"]
    assert set(doc["arms"]) == {"A", "C"} and doc["finished"]
    runs = doc["runs"]
    assert len(runs) == 4 and sum(r["warmup"] for r in runs) == 2
    for r in runs:
        assert r["pass"] is True, r
        assert r["cost_usd"] == pytest.approx(0.05) and r["num_turns"] == 3
        assert r["usage"]["cache_read_input_tokens"] == 62000
        assert r["transcript"] == {"requests": 3, "peak_context": 57010, "median_context": 32010,
                                   "cache_breaks": 1, "subagent_requests": 0,
                                   "tokens": {"input_tokens": 30, "cache_read_input_tokens": 62000,
                                              "cache_creation_input_tokens": 57000, "output_tokens": 150}}
        assert r["infra_error"] is None and r["agent_error"] is None and r["billed"] is True
        assert not os.path.exists(r["workdir"])  # --keep none
    assert json.loads((work / "settings" / "C.json").read_text())["autoCompactWindow"] == 200000
    calls = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(calls) == 4
    for c in calls:
        assert c["env"]["CLAUDECODE"] is None and c["env"]["DISABLE_AUTOUPDATER"] == "1"
        assert ("--plugin-dir" in c["argv"]) == (c["env"]["HETZ_SQUEEZE"] == "1")
        assert c["argv"][c["argv"].index("--max-turns") + 1] == "30"
    # the report reads what run.py wrote
    rep = subprocess.run([sys.executable, os.path.join(BENCH, "report.py"), str(results), "--json",
                          "--iters", "100"], capture_output=True, text=True, check=True)
    summary = json.loads(rep.stdout)
    assert summary["runs_used"] == 2 and summary["excluded"]["warmup"] == 2


def test_execute_refuses_wrong_version(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text(f"#!{sys.executable}\nprint('9.9.9 (Claude Code)')\n")
    fake.chmod(0o755)
    p = dry("--execute", "--claude", str(fake), "--arms", "A", "--tasks", "repo-qa", "-k", "1",
            "--out", str(tmp_path / "r"), "--work-root", str(tmp_path / "hetz-bench"))
    assert p.returncode == 2 and "expected '2.1.272'" in p.stderr
    assert not (tmp_path / "r").exists()


def test_unbilled_errors_are_infra_errors_and_abort(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text(f"""#!{sys.executable}
import json, sys
if sys.argv[1:] == ["--version"]:
    print("2.1.272 (Claude Code)"); sys.exit(0)
print(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "num_turns": 1,
                  "total_cost_usd": 0, "session_id": "x", "result": "Failed to authenticate"}}))
sys.exit(1)
""")
    fake.chmod(0o755)
    out_dir = tmp_path / "r"
    p = dry("--execute", "--claude", str(fake), "--arms", "A", "--tasks", "failing-test", "-k", "4",
            "--warmup", "0", "--seed", "1", "--max-errors", "2", "--out", str(out_dir),
            "--work-root", str(tmp_path / "hetz-bench"), "--keep", "none")
    assert p.returncode == 0
    assert "aborting: 2 runs in a row" in p.stderr
    doc = json.loads(next(out_dir.glob("*.json")).read_text())
    assert len(doc["runs"]) == 2
    for r in doc["runs"]:
        assert r["infra_error"].startswith("api error: Failed to authenticate")
        assert r["pass"] is False


# ---------------------------------------------------------------- variants, error classes, resume cost


def test_delegate_variant_prefixes_and_adds_approval_turn(tmp_path):
    a = arm("D-delegate")
    assert a["variant_of"] == "D" and a["plugin_dirs"] == arm("D")["plugin_dirs"]
    assert arm("D")["prompt_prefix"] == "" and arm("D")["extra_turns"] == [] and arm("D")["variant_of"] is None
    t = bench.load_task("failing-test")
    inv = bench.plan_invocations(a, t)
    assert [(k, m) for k, _, _, m, _ in inv] == [("turn", "new"), ("turn", "resume")]
    assert inv[0][2] == "/hetz-crew:delegate " + t["turns"][0]
    assert inv[1][2] == a["extra_turns"][0]
    for name in ("D-sonnet", "D-clear", "D-handoff"):
        assert arm(name)["variant_of"] == "D"
    p = dry("--arms", "D-delegate", "--tasks", "failing-test", "-k", "1", "--warmup", "0", "--seed", "2",
            "--work-root", str(tmp_path / "w"))
    assert p.returncode == 0, p.stderr
    lines = claude_lines(p.stdout)
    assert len(lines) == 2 and "-p '/hetz-crew:delegate The test suite" in lines[0] and "--resume" in lines[1]
    with pytest.raises(bench.BenchError, match="extra_turns"):
        bench.resolve_arm("x", {"x": {"extra_turns": ["--oops"]}})


def test_invocation_delta_subtracts_restored_cost_state():
    res = {"total_cost_usd": 0.30, "modelUsage": {"m": {"inputTokens": 30, "costUSD": 0.30},
                                                   "h": {"inputTokens": 5, "costUSD": 0.01}}}
    restored = {"type": "cost-state", "totalCostUSD": 0.20, "modelUsage": {"m": {"inputTokens": 20, "costUSD": 0.2}}}
    cost, mu = bench.invocation_delta(res, restored)
    assert cost == pytest.approx(0.10)
    assert mu["m"]["inputTokens"] == 10 and mu["m"]["costUSD"] == pytest.approx(0.10)
    assert mu["h"] == {"inputTokens": 5, "costUSD": 0.01}
    assert bench.invocation_delta(res, None) == (0.30, res["modelUsage"])
    # reported below the restored total: nothing was restored, keep the report
    assert bench.invocation_delta({"total_cost_usd": 0.05}, restored)[0] == pytest.approx(0.05)
    assert bench.invocation_delta({}, restored)[0] is None


RESUME_CLAUDE = """
#!PYTHON
# Mimics 2.1.272 --resume: restores the cost-state from the transcript, reports cumulative totals.
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.272 (Claude Code)"); sys.exit(0)
sid = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
proj = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "fake")
os.makedirs(proj, exist_ok=True)
path = os.path.join(proj, sid + ".jsonl")
total, tokens = 0.0, 0
if "--resume" in args and os.path.exists(path):
    for line in open(path):
        rec = json.loads(line)
        if rec.get("type") == "cost-state":
            total, tokens = rec["totalCostUSD"], rec["modelUsage"]["m"]["inputTokens"]
total, tokens = total + 0.10, tokens + 100
with open(path, "a") as f:
    f.write(json.dumps({"type": "cost-state", "sessionId": sid, "totalCostUSD": total,
                        "modelUsage": {"m": {"inputTokens": tokens, "outputTokens": 0, "costUSD": total}}}) + "\\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                  "total_cost_usd": total, "session_id": sid, "result": "ok",
                  "usage": {"input_tokens": 100},
                  "modelUsage": {"m": {"inputTokens": tokens, "outputTokens": 0, "costUSD": total}}}))
"""


def _fake(tmp_path, body):
    fake = tmp_path / "claude"
    fake.write_text(textwrap.dedent(body).lstrip().replace("PYTHON", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def test_resumed_turns_are_not_double_counted(tmp_path):
    fake = _fake(tmp_path, RESUME_CLAUDE)
    out_dir = tmp_path / "r"
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"))
    p = dry("--execute", "--claude", str(fake), "--arms", "A", "--tasks", "three-tasks", "-k", "1", "--warmup", "0",
            "--seed", "1", "--out", str(out_dir), "--work-root", str(tmp_path / "hetz-bench"), "--keep", "none",
            env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    (r,) = json.loads(next(out_dir.glob("*.json")).read_text())["runs"]
    assert [i["reported_cost_usd"] for i in r["invocations"]] == pytest.approx([0.1, 0.2, 0.3])
    assert [i["cost_usd"] for i in r["invocations"]] == pytest.approx([0.1, 0.1, 0.1])
    assert r["cost_usd"] == pytest.approx(0.3)  # not 0.6
    assert r["model_usage"]["m"]["inputTokens"] == 300


SLOW_CLAUDE = """
#!PYTHON
import sys, time
if sys.argv[1:] == ["--version"]:
    print("2.1.272 (Claude Code)"); sys.exit(0)
time.sleep(30)
"""


def test_timeout_is_a_failed_run_with_unknown_cost(tmp_path):
    fake = _fake(tmp_path, SLOW_CLAUDE)
    out_dir = tmp_path / "r"
    p = dry("--execute", "--claude", str(fake), "--arms", "A", "--tasks", "failing-test", "-k", "1",
            "--warmup", "0", "--seed", "1", "--timeout", "1", "--out", str(out_dir),
            "--work-root", str(tmp_path / "hetz-bench"), "--keep", "none")
    assert p.returncode == 0, p.stderr
    (r,) = json.loads(next(out_dir.glob("*.json")).read_text())["runs"]
    assert r["infra_error"] is None and r["agent_error"] == "timeout after 1s"
    assert r["pass"] is False and r["cost_usd"] is None and r["billed"] is False
    assert r["check_pass"] is False  # the check still ran


def test_missing_claude_is_infra_and_matrix_survives(tmp_path):
    out_dir = tmp_path / "r"
    p = dry("--execute", "--claude", str(tmp_path / "no-such-claude"), "--expect-version", "", "--arms", "A",
            "--tasks", "failing-test", "-k", "3", "--warmup", "0", "--seed", "1", "--max-errors", "2",
            "--out", str(out_dir), "--work-root", str(tmp_path / "hetz-bench"), "--keep", "none")
    assert p.returncode == 0, p.stderr
    assert "aborting: 2 runs in a row" in p.stderr
    doc = json.loads(next(out_dir.glob("*.json")).read_text())
    assert doc["finished"] and len(doc["runs"]) == 2
    assert all(r["infra_error"].startswith("cannot run claude") for r in doc["runs"])


def test_execute_run_records_bench_exceptions(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(bench, "clean_env", boom)
    run = {"order": 0, "arm": "A", "task": "failing-test", "rep": 0, "warmup": False}
    rec = bench.execute_run(run, arm("A"), bench.load_task("failing-test"), None, str(tmp_path))
    assert rec["infra_error"] == "bench error: RuntimeError('boom')"
    assert rec["pass"] is False and rec["finished"]


def test_work_root_must_carry_the_bench_marker(tmp_path):
    p = dry("--execute", "--claude", str(tmp_path / "x"), "--expect-version", "", "--arms", "A", "--tasks",
            "repo-qa", "-k", "1", "--out", str(tmp_path / "r"), "--work-root", str(tmp_path / "plain"))
    assert p.returncode == 2 and "must contain 'hetz-bench'" in p.stderr
    assert not (tmp_path / "plain").exists() and not (tmp_path / "r").exists()
