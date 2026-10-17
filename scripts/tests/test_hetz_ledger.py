"""Tests for scripts/hetz_ledger.py on synthetic transcripts (self-contained: no conftest, no real ~/.claude)."""
import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "hetz_ledger.py"
_spec = importlib.util.spec_from_file_location("hetz_ledger_under_test", SCRIPT)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

SECRET = "SECRETxyzzy"
PRICES = {"_source": "test", "_date": "2026-01-01",
          "_over_100k": {"claude-cheap": {"input": 5, "output": 50}},
          "models": {
              "claude-test": {"input": 1, "cache_write_5m": 2, "cache_write_1h": 3, "cache_read": 0.5, "output": 10},
              "claude-test-big": {"input": 100, "cache_write_5m": 100, "cache_write_1h": 100, "cache_read": 100, "output": 100},
              "claude-cheap": {"input": 1, "cache_write_5m": 1, "cache_write_1h": 1, "cache_read": 1, "output": 10}}}
BASE = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc).timestamp()


def stamp(ts):
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def asst(mid, ts, session="s1", model="claude-test", inp=0, cr=0, cw5=0, cw1=0, out=0, blocks=None, side=False,
         agent=None, version="2.1.272", entry="cli", effort=None, reason=None, speed="standard", web=0, cwd="/w/proj"):
    usage = {"input_tokens": inp, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw5 + cw1,
             "cache_creation": {"ephemeral_5m_input_tokens": cw5, "ephemeral_1h_input_tokens": cw1},
             "output_tokens": out, "speed": speed,
             "server_tool_use": {"web_search_requests": web, "web_fetch_requests": 0}}
    msg = {"id": mid, "type": "message", "role": "assistant", "model": model, "usage": usage,
           "content": blocks if blocks is not None else [{"type": "text", "text": "reply " + SECRET}]}
    if reason:
        msg["diagnostics"] = {"cache_miss_reason": {"type": reason}}
    rec = {"type": "assistant", "timestamp": stamp(ts), "sessionId": session, "message": msg, "isSidechain": side,
           "version": version, "entrypoint": entry, "cwd": cwd, "uuid": mid + "-u"}
    if effort:
        rec["effort"] = effort
    if agent:
        rec["attributionAgent"] = agent
    return rec


def bash_use(tid, command):
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command, "description": SECRET}}


def result(tid, text, ts, session="s1", is_error=False, as_list=False):
    content = [{"type": "text", "text": text}] if as_list else text
    return {"type": "user", "timestamp": stamp(ts), "sessionId": session, "cwd": "/w/proj",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content,
                                                     "is_error": is_error}]},
            "toolUseResult": {"stdout": text, "stderr": ""}}


def prompt(ts, session="s1"):
    return {"type": "user", "timestamp": stamp(ts), "sessionId": session, "cwd": "/w/proj",
            "message": {"role": "user", "content": "please do " + SECRET}}


def write(path, recs, raw_lines=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        for line in raw_lines:
            f.write(line + "\n")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated config dir: projects/, prices.json; nothing from the real ~/.claude is read."""
    cfg = tmp_path / "cfg"
    (cfg / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(PRICES))

    class Env:
        root = cfg / "projects"
        proj = cfg / "projects" / "-w-proj"
        prices_path = prices
        tmp = tmp_path
        config = cfg

        def run(self, *argv, prices=True):
            args = ["--projects-dir", str(self.root)] + (["--prices", str(self.prices_path)] if prices else []) + list(argv)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = L.main(args)
            return code, out.getvalue(), err.getvalue()

        def json(self, *argv, **kw):
            code, out, _ = self.run("--json", *argv, **kw)
            assert code in (0, 3)
            return json.loads(out)
    return Env()


# ---------------------------------------------------------------- pricing and dedupe

def test_chunk_records_deduped_by_message_id(env):
    recs = [asst("m1", BASE, inp=1_000_000, cr=2_000_000, cw5=1_000_000, cw1=1_000_000, out=5,
                 blocks=[{"type": "thinking", "thinking": SECRET}]),
            asst("m1", BASE + 1, inp=1_000_000, cr=2_000_000, cw5=1_000_000, cw1=1_000_000, out=5),
            asst("m1", BASE + 2, inp=1_000_000, cr=2_000_000, cw5=1_000_000, cw1=1_000_000, out=1_000_000)]
    write(env.proj / "s1.jsonl", recs)
    rep = env.json()
    assert rep["totals"]["requests"] == 1
    split = rep["spend_by_type"]
    assert split["output"]["tokens"] == 1_000_000
    assert split["cache_read"]["tokens"] == 2_000_000
    # 1*1 + 2*1 + 3*1 + 0.5*2 + 10*1 = 17
    assert rep["totals"]["cost"] == pytest.approx(17.0)
    assert split["output"]["share"] == pytest.approx(100 * 10 / 17, abs=0.01)


def test_same_message_and_tool_ids_across_forked_files_counted_once(env):
    recs = [asst("m1", BASE, out=1_000_000, blocks=[bash_use("t1", "ls -la")]), result("t1", "x" * 100, BASE + 1)]
    write(env.proj / "s1.jsonl", recs)
    write(env.proj / "s2.jsonl", recs)       # --fork-session copies history
    rep = env.json()
    assert rep["totals"]["requests"] == 1
    assert rep["bash"]["calls"] == 1


def test_longest_prefix_over_100k_fast_web_and_fallback(env, tmp_path):
    p = L.Prices(str(env.prices_path))
    assert p.match("claude-test-big-20260101") == "claude-test-big"
    assert p.match("claude-test-20260101") == "claude-test"
    assert p.match("us.anthropic.claude-test-big") == "claude-test-big"
    assert p.match("gpt-4") is None
    assert p.rates("claude-cheap", 100_001)["input"] == 5
    assert p.rates("claude-cheap", 100_001)["cache_read"] == 1      # keys absent from the tier keep base rates
    assert p.rates("claude-cheap", 99_999)["input"] == 1
    fb = L.Prices(str(tmp_path / "missing.json"))
    assert fb.source == "built-in fallback"
    assert fb.match("claude-opus-5-5") == "claude-opus-5-5" and fb.match("claude-opus-5-20260101") == "claude-opus-5"
    assert fb.rates("claude-haiku-5-5", 200_000)["input"] == 0.5
    assert set(L.RATE_KEYS) <= set(fb.models["claude-sonnet-5-5"])

    write(env.proj / "s1.jsonl", [asst("m1", BASE, inp=1_000_000, speed="fast", web=3),
                                  asst("m2", BASE + 9, model="mystery-model", inp=1_000_000)])
    rep = env.json()
    assert rep["spend_by_type"]["input"]["cost"] == pytest.approx(1.0)          # no fast premium off Opus 4.6-5
    assert rep["spend_by_type"]["web_search"]["cost"] == pytest.approx(0.03)
    assert rep["unpriced_models"] == {"mystery-model": 1}


def test_fast_geo_and_advisor_priced_like_claude_code(env):
    assert L.fast_mult("claude-opus-4-6") == 6 and L.fast_mult("us.anthropic.claude-opus-4-7-20260101-v1:0") == 6
    assert L.fast_mult("claude-opus-5-20260101") == 2 and L.fast_mult("claude-opus-4-8@20260101") == 2
    assert L.fast_mult("claude-opus-5-5") == 1 and L.fast_mult("claude-sonnet-5") == 1
    rec = asst("m1", BASE, inp=1_000_000, speed="fast")
    rec["message"]["usage"]["inference_geo"] = "us"
    rec["message"]["usage"]["iterations"] = [
        {"type": "message", "input_tokens": 1_000_000, "output_tokens": 0},
        {"type": "advisor_message", "model": "claude-cheap", "input_tokens": 0, "output_tokens": 100_000},
        {"type": "advisor_message", "model": "mystery-advisor", "input_tokens": 5}]
    write(env.proj / "s1.jsonl", [rec, dict(rec, timestamp=stamp(BASE + 1))])     # chunk record: counted once
    rep = env.json()
    assert rep["totals"]["requests"] == 1
    assert rep["totals"]["cost"] == pytest.approx(1.1 + 1.1)     # 1M in x $1 x 1.1 (us) + advisor 100k out x $10 x 1.1
    models = {m["model"]: m for m in rep["by_model"]}
    assert models["claude-test"]["cost"] == pytest.approx(1.1) and models["claude-cheap"]["cost"] == pytest.approx(1.1)
    assert rep["spend_by_type"]["output"]["tokens"] == 100_000
    assert rep["unpriced_models"] == {"mystery-advisor": 1}


def test_context_buckets(env):
    sizes = [10_000, 100_000, 200_000, 400_000, 700_000]
    write(env.proj / "s1.jsonl", [asst("m%d" % i, BASE + 60 * i, cr=s, out=1) for i, s in enumerate(sizes)])
    rep = env.json()
    assert [b["requests"] for b in rep["context_buckets"]] == [1, 1, 1, 1, 1]
    assert [b["bucket"] for b in rep["context_buckets"]] == ["<50k", "50-150k", "150-300k", "300-600k", ">600k"]
    assert rep["context"]["max"] == 700_000
    assert rep["context"]["share_over_300k"] == pytest.approx(100 * 0.55 / 0.705, abs=0.1)


# ---------------------------------------------------------------- cache breaks

def test_cache_break_causes(env):
    t = BASE
    recs = [
        asst("r1", t, cw1=50_000, out=1),                                        # cold start: not an event
        asst("r2", t + 60, cr=50_000, cw1=30_000, out=1),                        # prefix reused: big_append
        asst("r3", t + 120, model="claude-test-big", cw1=80_000, out=1),         # model change
        {"type": "system", "subtype": "compact_boundary", "timestamp": stamp(t + 150), "sessionId": "s1"},
        asst("r4", t + 180, model="claude-test-big", cr=5_000, cw1=30_000, out=1),   # after compaction
        asst("r5", t + 180 + 7200, model="claude-test-big", cw1=40_000, out=1),      # 2h gap, 1h TTL
        asst("r6", t + 7500, model="claude-test-big", cw1=40_000, out=1, version="2.1.300"),  # resume/upgrade
        asst("r7", t + 7560, model="claude-test-big", cr=40_000, cw1=10_000, out=1, version="2.1.300"),  # small
        asst("r8", t + 7600, model="claude-test-big", cw1=45_000, out=1, version="2.1.300", reason="system_changed"),
        asst("r9", t + 7650, model="claude-test-big", cw1=45_000, out=1, version="2.1.300", effort="high"),
    ]
    recs[-2]["effort"] = "low"
    write(env.proj / "s1.jsonl", recs)
    rep = env.json()
    causes = {c["cause"]: c["events"] for c in rep["cache_breaks"]["by_cause"]}
    assert causes == {"big_append": 1, "model_change": 1, "compaction": 1, "ttl_expired_1h": 1, "resume": 1,
                      "api:system_changed": 1, "effort_change": 1}
    assert rep["cache_breaks"]["events"] == 6          # big_append is reported but not counted as a break
    assert rep["cache_breaks"]["big_appends"] == 1
    top = rep["cache_breaks"]["top"][0]
    assert top["model"] == "claude-test-big" and top["session"] == "s1"


def test_ttl_5m_for_threads_without_1h_writes(env):
    write(env.proj / "s1.jsonl", [asst("a", BASE, cw5=30_000, out=1), asst("b", BASE + 400, cw5=30_000, out=1)])
    causes = {c["cause"] for c in env.json()["cache_breaks"]["by_cause"]}
    assert causes == {"ttl_expired_5m"}


# ---------------------------------------------------------------- subagents

def test_subagent_cost_by_type_and_model(env):
    write(env.proj / "s1.jsonl", [asst("main1", BASE, cr=100_000, out=10)])
    sub = env.proj / "s1" / "subagents"
    write(sub / "agent-a1.jsonl", [asst("x1", BASE + 5, model="claude-cheap", cw5=20_000, out=1, side=True, agent="Explore"),
                                   asst("x2", BASE + 9, model="claude-cheap", cr=20_000, out=1, side=True, agent="Explore")])
    write(sub / "agent-b2.jsonl", [asst("y1", BASE + 5, cw5=30_000, out=1, side=True)])
    (sub / "agent-b2.meta.json").write_text(json.dumps({"agentType": "hetz-crew:runner", "description": SECRET}))
    write(sub / "workflows" / "wf_1" / "agent-c3.jsonl", [asst("z1", BASE + 5, cw5=1000, out=1, side=True)])
    rep = env.json()
    rows = {(r["agent"], r["model"]): r for r in rep["subagents"]["by_type_model"]}
    assert rows[("Explore", "claude-cheap")]["runs"] == 1
    assert rows[("Explore", "claude-cheap")]["requests"] == 2
    assert rows[("Explore", "claude-cheap")]["cold_start_tokens_avg"] == 20_000
    assert rows[("hetz-crew:runner", "claude-test")]["cold_start_tokens_avg"] == 30_000
    assert ("subagent", "claude-test") in rows
    assert rep["subagents"]["runs"] == 3
    assert rep["sessions"]["count"] == 1              # subagents roll up into their parent session
    assert 0 < rep["subagents"]["share"] < 100


# ---------------------------------------------------------------- bash families

@pytest.mark.parametrize("command,fam", [
    ("cd /a/b && git status --short", "git status"),
    ("FOO=1 BAR=2 npm test -- --watch", "npm test"),
    ("python3 -m pytest -q tests/", "python3 -m pytest"),
    ("sed -n '1,200p' /private/%s/file.py" % SECRET, "sed"),
    ("/usr/bin/env python3 /x/%s.py" % SECRET, "python3"),
    ("timeout 30 make build", "make build"),
    ("git /weird/%s" % SECRET, "git"),
    ("(cd sub && cargo test)", "cargo test"),
    ("./scripts/check.sh --fast", "check.sh"),
    ("export X=1; uv run pytest", "uv run"),
    ("python3 -c 'import os\nprint(1)'", "python3"),
    ("grep -E '^%s_KEY=|^B_KEY=' .env" % SECRET, "grep"),          # quoted '|' is not a pipe
    ("for f in *.py; do wc -l \"$f\"; done", "wc"),
    ("X=$(mktemp -d /tmp/%s.XXXX) && ls $X" % SECRET, "mktemp"),
    ("$C --version", "(variable)"),
    ("[ -f x ] && echo y", "test"),
    ("cat > f <<'EOF'\n%s ' unbalanced\nEOF" % SECRET, "cat"),
    ("curl \"https://x/%s?a=1&b=2\" | head" % SECRET, "curl"),
    ("2>/dev/null ls", "ls"),
    ("# %s\nnpm run build" % SECRET, "npm run"),
    ("rg %s ." % SECRET.lower(), "rg"),                         # search patterns are never a family
    ("68f0b23c-dd20-4704 --x", "(other)"),
    ("^### `%s` x" % SECRET, "(other)"),
    ("! grep -q x f", "grep"),
    ("export P='a|^%s=b'; ls" % SECRET, "ls"),                  # separators inside a skipped setup command
    ("cd '/x;%s' && ls" % SECRET, "ls"),
    ("", "(none)"),
    # data words never become a family
    ("files=(\n  %s\n)\nls" % SECRET, "ls"),                    # array literal
    ("declare -a A=(\n %s\n)" % SECRET, "(none)"),
    ("case $x in\n  %s) ls;;\nesac" % SECRET, "(none)"),          # case patterns
    ("for f in \\\n  %s x; do ls; done" % SECRET, "ls"),         # backslash-continued for-list
    ("%s() {\n  echo hi\n}" % SECRET, "echo"),                   # function definition name
    (": <<EOF\n%s\nEOF" % SECRET, "(none)"),                     # here-doc body after a setup command
    ("sudo -u %s ls" % SECRET, "ls"),                             # wrapper option arguments
    ("env -u %s FOO=1 git status" % SECRET, "git status"),
    ("timeout -s KILL 1.5 make", "make"),
    ("%s --flag" % SECRET, "(other)"),                            # not a known command, not on PATH
    ("ls $(pwd)", "ls"),
])
def test_family(command, fam):
    assert L.family(command) == fam


def test_bash_families_ranked_by_result_chars(env):
    t = BASE
    recs = [asst("m1", t, out=1, blocks=[bash_use("t1", "cat big.txt"), bash_use("t2", "git status")]),
            result("t1", "a" * 5000, t + 1), result("t2", "b" * 100, t + 1, as_list=True),
            asst("m2", t + 5, out=1, blocks=[bash_use("t3", "git status")]), result("t3", "Exit code 1\nboom", t + 6, is_error=True),
            asst("m3", t + 7, out=1, blocks=[{"type": "tool_use", "id": "t4", "name": "Read", "input": {"file_path": "/x"}}]),
            result("t4", "c" * 9999, t + 8)]
    write(env.proj / "s1.jsonl", recs)
    b = env.json()["bash"]
    assert b["calls"] == 3 and b["errors"] == 1 and b["over_2k"] == 1
    assert [f["family"] for f in b["by_family"]] == ["cat", "git status"]
    assert b["by_family"][1]["calls"] == 2


# ---------------------------------------------------------------- sessions and filters

def test_sessions_over_5(env):
    write(env.proj / "big.jsonl", [asst("b1", BASE, session="big", out=1_000_000)])       # $10
    write(env.proj / "small.jsonl", [asst("s1", BASE, session="small", out=100_000)])     # $1
    rep = env.json()
    s = rep["sessions"]
    assert s["count"] == 2 and s["over_5"]["count"] == 1
    assert s["over_5"]["cost"] == pytest.approx(10.0)
    assert rep["totals"]["cost_per_session"] == pytest.approx(5.5)
    assert rep["totals"]["active_days"] == 1


def test_since_project_session_filters(env):
    now = time.time()
    write(env.proj / "new.jsonl", [asst("n1", now - 3600, session="newsess", out=1000)])
    write(env.proj / "old.jsonl", [asst("o1", now - 10 * 86400, session="oldsess", out=1000)])
    other = env.root / "-w-other"
    write(other / "x.jsonl", [asst("x1", now - 3600, session="othersess", out=1000, cwd="/w/other")])
    assert env.json()["totals"]["requests"] == 3
    assert env.json("--since", "2d")["totals"]["requests"] == 2
    assert env.json("--since", "2d", "--until", "2h")["totals"]["requests"] == 0
    assert env.json("--project", "other")["totals"]["requests"] == 1
    assert env.json("--project", "/w/proj")["totals"]["requests"] == 2      # a path is encoded like the dir name
    assert env.json("--session", "oldS")["totals"]["requests"] == 1
    code, out, err = env.run("--since", "yesterdayish")
    assert code == 2 and "bad time" in err


def test_bench_fixture_sessions_skipped_unless_asked_for(env):
    write(env.proj / "real.jsonl", [asst("r1", BASE, session="real", out=1000)])
    bench = env.root / "-private-var-T-hetz-bench-20261017-repo-qa-A-1-repo"
    write(bench / "b.jsonl", [asst("b1", BASE, session="bench", out=1000, cwd="/T/hetz-bench-x/repo")])
    assert env.json()["totals"]["requests"] == 1
    assert env.json("--project", "hetz")["totals"]["requests"] == 0     # -w-proj does not match; bench still skipped
    assert env.json("--project", "hetz-bench")["totals"]["requests"] == 1


def test_since_skips_files_untouched_since_window(env):
    old = write(env.proj / "old.jsonl", [asst("o1", time.time() - 3600, out=1000)])
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    assert env.json("--since", "7d")["files_scanned"] == 0


def test_compare_two_ranges(env):
    d1 = dt.datetime(2026, 9, 10, 12).timestamp()
    d2 = dt.datetime(2026, 10, 10, 12).timestamp()
    write(env.proj / "a.jsonl", [asst("a1", d1, session="a", out=1_000_000), asst("a2", d1 + 60, session="a", out=1_000_000)])
    write(env.proj / "b.jsonl", [asst("b1", d2, session="b", out=1_000_000)])
    rep = env.json("--compare", "2026-09-01..2026-09-30", "2026-10-01..2026-10-31")
    delta = {r["metric"]: r for r in rep["delta"]}
    assert delta["cost"]["a"] == pytest.approx(20) and delta["cost"]["b"] == pytest.approx(10)
    assert delta["cost"]["delta_pct"] == pytest.approx(-50)
    assert delta["requests"]["a"] == 2 and delta["requests"]["b"] == 1
    code, out, _ = env.run("--compare", "2026-09-01..2026-09-30", "2026-10-01..")
    assert code == 0 and "$/active day" in out
    code, _, err = env.run("--compare", "2026-09-01", "2026-10-01..")
    assert code == 2 and "bad range" in err
    for extra in (["--replay-squeeze"], ["--since", "2d"], ["--until", "2026-10-02"]):
        code, out, err = env.run("--compare", "2026-09-01..2026-09-30", "2026-10-01..", *extra)
        assert code == 2 and "cannot be combined" in err and not out


# ---------------------------------------------------------------- squeeze replay

STUB = r'''
import os, re
KEEP_RE = re.compile(r"error|FAIL")
FOOTER_EST = 50
CALLS = []
def squeeze(command, stdout, stderr):
    CALLS.append(os.environ.get("CLAUDE_PLUGIN_DATA"))
    assert os.environ.get("HETZ_SQUEEZE") is None
    if command.startswith("npm test"):
        lines = stdout.splitlines()
        kept = [l for l in lines if KEEP_RE.search(l)]
        out = "\n".join(kept + ["[%d lines squeezed]" % (len(lines) - len(kept))])
        return {"stdout": out, "stderr": "", "lines_in": len(lines), "lines_out": len(kept) + 1,
                "chars_in": len(stdout), "chars_out": len(out), "tip": ""}
    if command.startswith("make lossy"):
        return {"stdout": "", "stderr": "", "lines_in": 1, "lines_out": 0, "chars_in": len(stdout), "chars_out": 0, "tip": "t"}
    if command.startswith("boom"):
        raise RuntimeError("bad")
    return None
'''


def replay_fixture(env, lossy=False):
    stub = env.tmp / "stub_squeeze.py"
    stub.write_text(STUB)
    t = BASE
    noisy = "\n".join(["ok line %d %s" % (i, SECRET) for i in range(200)] + ["1 error: " + SECRET, "FAIL test_x"])
    recs = [asst("m1", t, out=1, blocks=[bash_use("t1", "npm test --silent"), bash_use("t2", "git diff"),
                                         bash_use("t3", "npm test"), bash_use("t4", "boom now")]),
            result("t1", noisy, t + 1), result("t2", "diff " + SECRET, t + 1),
            result("t3", "Exit code 1\n" + noisy, t + 1, is_error=True), result("t4", "zzz", t + 1)]
    if lossy:
        recs += [asst("m2", t + 9, out=1, blocks=[bash_use("t5", "make lossy")]), result("t5", "error: kept? " + SECRET, t + 10)]
    write(env.proj / "s1.jsonl", recs)
    return stub, noisy


def test_replay_squeeze_reports_savings_and_zero_keep_loss(env, monkeypatch):
    monkeypatch.setenv("HETZ_SQUEEZE", "0")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(env.tmp / "real-data"))
    stub, noisy = replay_fixture(env)
    code, out, err = env.run("--replay-squeeze", "--squeeze", str(stub), "--json")
    assert code == 0, err
    rp = json.loads(out)["replay"]
    assert rp["results"] == 4 and rp["errors_skipped"] == 1 and rp["squeezed"] == 1
    assert rp["passthrough"] == 1 and rp["exceptions"] == 1 and rp["keep_lost"] == 0
    fams = {f["family"]: f for f in rp["by_family"]}
    shown = "\n".join(["1 error: " + SECRET, "FAIL test_x", "[200 lines squeezed]"])
    assert fams["npm test"]["chars_saved"] == len(noisy) - len(shown) - 50     # footer counted
    assert fams["git diff"]["chars_saved"] == 0
    assert rp["chars_saved"] == fams["npm test"]["chars_saved"]
    assert rp["gate"]["pass"] is False and any("days" in r for r in rp["gate"]["reasons"])
    # the live switches are restored after the replay and side-writes went to a throwaway dir
    assert os.environ["HETZ_SQUEEZE"] == "0" and os.environ["CLAUDE_PLUGIN_DATA"] == str(env.tmp / "real-data")
    code, text, _ = env.run("--replay-squeeze", "--squeeze", str(stub))
    assert "KEEP lines lost: 0" in text and "npm test" in text


def test_replay_squeeze_flags_keep_loss_with_exit_3(env):
    stub, _ = replay_fixture(env, lossy=True)
    code, out, _ = env.run("--replay-squeeze", "--squeeze", str(stub), "--json")
    rp = json.loads(out)["replay"]
    assert code == 3
    assert rp["keep_lost"] == 1 and rp["gate"]["pass"] is False
    assert rp["keep_lost_events"] == [{"session": "s1", "tool_use_id": "t5", "family": "make lossy", "lines": 1}]


def test_replay_missing_or_broken_squeeze(env):
    replay_fixture(env)
    code, _, err = env.run("--replay-squeeze", "--squeeze", str(env.tmp / "nope.py"))
    assert code == 2 and "cannot load squeeze" in err
    bad = env.tmp / "bad.py"
    bad.write_text("import sys\nsys.exit(0)\n")
    code, _, err = env.run("--replay-squeeze", "--squeeze", str(bad))
    assert code == 2 and "cannot load squeeze" in err


def test_keep_lost_normalises_ansi_and_whitespace():
    import re
    keep = re.compile(r"error")
    assert L.keep_lost(keep, "\x1b[31merror: x\x1b[0m  \nok", "error: x") == 0
    assert L.keep_lost(keep, "error: x\nerror: y", "error: x") == 1


def test_keep_lost_matches_squeeze_rendering():
    import re
    keep = re.compile(r"error|warn")
    text_in = "warn: a\nwarn: a\nwarn: a\nerror: b\n50%\rerror: c\x1b]8;;file:///u\x07 \nok"
    assert L.keep_lost(keep, text_in, "warn: a (×3)\nerror: b\nerror: c") == 0     # folded run, \r, OSC link
    assert L.keep_lost(keep, text_in, "warn: a (×3)\nerror: c") == 1                 # error: b really dropped
    assert L.keep_lost(keep, text_in, "error: b\nerror: c") == 3                      # the folded run dropped
    assert L.keep_lost(keep, "error (×3)", "error (×3)") == 0                          # literal suffix in input


REAL_SQUEEZE = SCRIPT.parents[1] / "plugins" / "hetz" / "hooks" / "squeeze.py"


@pytest.mark.skipif(not REAL_SQUEEZE.exists(), reason="plugins/hetz/hooks/squeeze.py not present")
def test_replay_real_squeeze_folded_keep_lines_not_lost(env):
    noisy = "\n".join(["   Compiling crate%d v0.1.%d" % (i, i) for i in range(200)]
                      + ["\x1b]8;;file:///x\x07warning: unused variable x\x1b]8;;\x07"] * 4
                      + ["\x1b[33mwarning\x1b[0m: shadowed y"] * 3 + ["error\x0bvt kept", "Finished"])
    write(env.proj / "s1.jsonl", [asst("m1", BASE, out=1, blocks=[bash_use("t1", "cargo build"), bash_use("t2", "make")]),
                                  result("t1", noisy, BASE + 1), result("t2", noisy, BASE + 1)])
    code, out, err = env.run("--replay-squeeze", "--squeeze", str(REAL_SQUEEZE), "--json")
    rp = json.loads(out)["replay"]
    assert code == 0, err
    assert rp["squeezed"] == 2 and rp["exceptions"] == 0 and rp["keep_lost"] == 0
    assert rp["chars_saved"] > 0


# ---------------------------------------------------------------- privacy and robustness

def test_no_transcript_content_in_any_output(env):
    stub, _ = replay_fixture(env, lossy=True)
    leaky = ["cat /%s/notes" % SECRET, "grep -E 'a|^%s_K=' .env" % SECRET, "echo \"x; %s y\"" % SECRET,
             "curl 'https://h/?q=1&%s=2'" % SECRET, "cat <<EOF\n%s\nEOF" % SECRET,
             "export P='a|^%s=b'; ls" % SECRET, "cd '/x;%s' && ls" % SECRET]
    recs = [prompt(BASE, "s2"), asst("q1", BASE + 1, session="s2", out=10,
                                     blocks=[bash_use("q%d" % i, c) for i, c in enumerate(leaky)])]
    recs += [result("q%d" % i, SECRET * 10, BASE + 2, session="s2") for i in range(len(leaky))]
    write(env.proj / "s2.jsonl", recs)
    for argv in ([], ["--json"], ["--replay-squeeze", "--squeeze", str(stub)],
                 ["--replay-squeeze", "--squeeze", str(stub), "--json"],
                 ["--compare", "2026-09-01..2026-09-30", "2026-10-01.."]):
        _, out, err = env.run(*argv)
        assert SECRET not in out and SECRET not in err, argv


def test_garbage_lines_and_odd_records_are_tolerated(env):
    write(env.proj / "s1.jsonl",
          [asst("m1", BASE, out=1000), {"type": "assistant", "message": "not a dict"},
           {"type": "assistant", "message": {"id": "z", "model": "<synthetic>", "usage": {"output_tokens": 9}}},
           {"type": "assistant", "message": {"id": "nousage", "model": "claude-test"}},
           {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "ghost", "content": 5}]}},
           [1, 2, 3]],
          raw_lines=["{not json", "", "null"])
    (env.proj / "empty.jsonl").write_text("")
    rep = env.json()
    assert rep["totals"]["requests"] == 1 and rep["bad_lines"] == 1
    code, out, _ = env.run()
    assert code == 0 and "Spend by token type" in out


def test_malformed_record_fields_do_not_stop_the_report(env, monkeypatch):
    odd = [asst("m1", BASE, out=1000),
           {"type": "assistant", "timestamp": stamp(BASE + 1), "sessionId": {"x": 1}, "isSidechain": True, "agentId": [1],
            "message": {"id": ["unhashable"], "model": "claude-test", "usage": {"output_tokens": 1000}, "content": 7}},
           asst("m2", BASE + 2, out=1, blocks=[bash_use(["bad-id"], "ls"), bash_use("t1", "ls"), bash_use("t2", "ls")]),
           {"type": "user", "timestamp": stamp(BASE + 3), "message": {"content": [
               {"type": "tool_result", "tool_use_id": ["t1"], "content": "x"},
               {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": 5},
                                                                         {"type": "text", "text": "ok"}]}]}},
           result("t2", "boom", BASE + 4)]
    write(env.proj / "s1.jsonl", odd)
    real = L.result_text
    monkeypatch.setattr(L, "result_text", lambda c: (_ for _ in ()).throw(RuntimeError()) if c == "boom" else real(c))
    code, out, err = env.run("--json")
    assert code == 0, err
    rep = json.loads(out)
    assert rep["totals"]["requests"] == 3 and rep["bash"]["calls"] == 1 and rep["bash"]["chars"] == 2
    assert rep["bad_lines"] == 1              # the record that raised is counted, the rest of the file is read


def test_missing_projects_dir(env, tmp_path):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = L.main(["--projects-dir", str(tmp_path / "nowhere")])
    assert code == 2 and "no transcripts" in err.getvalue()


def test_empty_window_text_output(env):
    write(env.proj / "s1.jsonl", [asst("m1", BASE, out=1000)])
    code, out, _ = env.run("--session", "nomatch")
    assert code == 0 and "No requests in this window." in out


# ---------------------------------------------------------------- squeeze.tsv realized savings

def utc(ts):
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_squeeze_tsv_realized_savings(env):
    t = BASE
    main = [asst("m0", t, cr=10_000, cw1=100, out=1),
            asst("m1", t + 60, cr=10_000, out=1, blocks=[bash_use("b1", "npm test --x")]),
            result("b1", "squeezed", t + 91),
            asst("m2", t + 120, cr=10_000, out=1),
            asst("m3", t + 180, cr=10_000, out=1),
            {"type": "system", "subtype": "compact_boundary", "timestamp": stamp(t + 200), "sessionId": "s1"},
            asst("m4", t + 240, cr=10_000, out=1),
            asst("m5", t + 500, cr=10_000, out=1)]
    write(env.proj / "s1.jsonl", main)
    write(env.proj / "s1" / "subagents" / "agent-a1.jsonl",       # hooks in a subagent report the parent session id
          [asst("x1", t + 300, model="claude-cheap", cw5=1000, out=1, side=True, blocks=[bash_use("b2", "cargo build -r")]),
           result("b2", "squeezed", t + 301),
           asst("x2", t + 330, model="claude-cheap", cr=1000, out=1, side=True),
           asst("x3", t + 360, model="claude-cheap", cr=1000, out=1, side=True)])
    data = env.config / "plugins" / "data" / "hetz-hetz"
    data.mkdir(parents=True)
    (data / "squeeze.tsv").write_text(
        "ts\tsession_id\tcmd_head\tchars_in\tchars_out\n"
        "%s\ts1\tnpm test --x\t40000\t4000\n" % utc(t + 90) +     # main thread: m2, m3, then compaction
        "%s\ts1\tcargo build -r\t5000\t1000\n" % utc(t + 301) +   # subagent thread: x2, x3 only
        "%s\ts1\tpytest -q\t20000\t10000\n" % utc(t + 400) +        # no transcript result: next request only
        "%f\tother\tpytest -q\t1000\t1000\n" % (t + 90) +
        "garbage line\n")
    (env.config / "plugins" / "data" / "hetz-crew-hetz").mkdir()
    (env.config / "plugins" / "data" / "hetz-crew-hetz" / "squeeze.tsv").write_text("%f\ts1\tx\t9\t1\n" % t)
    sq = env.json()["squeeze_ledger"]
    assert sq["rows"] == 4 and sq["bad_rows"] == 2
    assert sq["matched_rows"] == 2 and sq["unmatched_rows"] == 2 and sq["lower_bound"] is True
    assert sq["chars_removed"] == 36000 + 4000 + 10000
    fam = {f["family"]: f["usd_saved_est"] for f in sq["by_family"]}
    assert fam["npm test"] == pytest.approx(9000 * (3 + 0.5) / 1e6, rel=1e-3)     # 1h write + 1 read, not m4/m5
    assert fam["cargo build"] == pytest.approx(1000 * (1 + 1) / 1e6, rel=1e-3)    # 5m write + 1 read at claude-cheap
    assert fam["pytest"] == pytest.approx(2500 * 3 / 1e6, rel=1e-3)                # one write at m5 (lower bound)
    assert sq["by_family"][0]["family"] == "npm test"
    assert env.json("--session", "other")["squeeze_ledger"]["rows"] == 1
    code, text, _ = env.run()
    assert "2 rows not found in transcripts" in text


def test_no_squeeze_tsv_means_none(env):
    write(env.proj / "s1.jsonl", [asst("m1", BASE, out=1000)])
    assert env.json()["squeeze_ledger"] is None


# ---------------------------------------------------------------- time parsing and CLI

def test_parse_when_and_range():
    now = dt.datetime(2026, 10, 17, 12).timestamp()
    assert L.parse_when("2d", now=now) == now - 2 * 86400
    assert L.parse_when("12h", now=now) == now - 12 * 3600
    assert L.parse_when("1w", now=now) == now - 7 * 86400
    assert L.parse_when("2026-10-01") == dt.datetime(2026, 10, 1).timestamp()
    assert L.parse_when("2026-10-01", end=True) == dt.datetime(2026, 10, 2).timestamp()
    assert L.parse_when("") is None
    a, b = L.parse_range("30d..14d", now=now)
    assert a == now - 30 * 86400 and b == now - 14 * 86400
    assert L.parse_range("..2026-10-01")[0] is None
    with pytest.raises(ValueError):
        L.parse_when("soon")


def test_cli_subprocess_isolated(env):
    write(env.proj / "s1.jsonl", [asst("m1", BASE, out=1_000_000)])
    run_env = dict(os.environ, CLAUDE_CONFIG_DIR=str(env.config))
    run_env.pop("CLAUDE_PLUGIN_DATA", None)
    proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--json", "--prices", str(env.prices_path)],
                          capture_output=True, text=True, env=run_env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["totals"]["cost"] == pytest.approx(10.0)
    proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "--replay-squeeze" in proc.stdout
