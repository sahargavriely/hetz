"""Tests for hooks/guard.py and hooks/prices.json. Self-contained: no conftest, temp dirs only."""
import importlib.util
import json
import os
import subprocess
import sys
import time

import pytest

HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(HOOKS, "guard.py")
RUN_SH = os.path.join(HOOKS, "run.sh")
SID = "11111111-2222-3333-4444-555555555555"


def load_guard():
    spec = importlib.util.spec_from_file_location("hetz_guard_under_test", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("HETZ_", "CLAUDE_")) and k != "ANTHROPIC_API_KEY"}
    for k in ("PAGER", "GIT_PAGER", "NO_COLOR", "CI", "PYTEST_ADDOPTS", "FORCE_COLOR"):
        e.pop(k, None)
    e["HOME"] = str(tmp_path / "home")
    e["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "data")
    e["CLAUDE_ENV_FILE"] = str(tmp_path / "sessionstart-hook-0.sh")
    (tmp_path / "home").mkdir()
    (tmp_path / "repo").mkdir()
    return e


def run(payload, env, script=GUARD, via_sh=False):
    data = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
    if isinstance(data, str):
        data = data.encode()
    cmd = ["sh", RUN_SH, os.path.basename(script)] if via_sh else [sys.executable, "-I", script]
    p = subprocess.run(cmd, input=data, env=env, capture_output=True, timeout=20)
    assert p.returncode == 0, p.stderr
    out = p.stdout.decode()
    if out:  # never plain stdout: one JSON object or nothing
        obj = json.loads(out)
        assert isinstance(obj, dict)
        return obj
    return None


def write_transcript(path, ctx, model="claude-opus-5-5", ttl="1h", first_ctx=None):
    lines = [{"type": "user", "message": {"role": "user", "content": "hi"}}]
    if first_ctx:
        lines.append({"type": "assistant", "message": {"role": "assistant", "model": model,
                      "usage": {"input_tokens": 3, "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": first_ctx - 3}}})
    cc = {"ephemeral_1h_input_tokens": 1000 if ttl == "1h" else 0,
          "ephemeral_5m_input_tokens": 1000 if ttl == "5m" else 0}
    lines.append({"type": "assistant", "message": {"role": "assistant", "model": model, "usage": {
        "input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": ctx - 1002,
        "output_tokens": 50, "cache_creation": cc}}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(path)


def append(path, *records):
    with open(path, "a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def assistant(ctx, model="claude-opus-5-5", **extra):
    rec = {"type": "assistant", "message": {"role": "assistant", "model": model, "usage": {
        "input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": ctx - 1002,
        "output_tokens": 50}}}
    rec.update(extra)
    return rec


def boundary(post=9_000):
    meta = {"trigger": "manual", "preTokens": 160_000}
    if post is not None:
        meta["postTokens"] = post
    return {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted",
            "level": "info", "compactMetadata": meta}


SUMMARY = {"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "summary of usage etc"}}


def set_commit_flag(env):
    flag = os.path.join(env["CLAUDE_PLUGIN_DATA"], "state", SID + ".commit")
    os.makedirs(os.path.dirname(flag), exist_ok=True)
    open(flag, "w").close()
    return flag


def stop(transcript, **kw):
    d = {"hook_event_name": "Stop", "session_id": SID, "cwd": "/tmp", "transcript_path": transcript,
         "stop_hook_active": False, "last_assistant_message": "done"}
    d.update(kw)
    return d


def ss(source, cwd, **kw):
    d = {"hook_event_name": "SessionStart", "source": source, "session_id": SID, "cwd": str(cwd),
         "transcript_path": "/nonexistent/t.jsonl"}
    d.update(kw)
    return d


def ups(transcript, prompt="do the thing", **kw):
    d = {"hook_event_name": "UserPromptSubmit", "session_id": SID, "cwd": "/tmp",
         "transcript_path": transcript, "prompt": prompt}
    d.update(kw)
    return d


def pms(ctx=142_000, to="claude-sonnet-5-5", **kw):
    d = {"hook_event_name": "PreModelSwitch", "session_id": SID, "cwd": "/tmp", "transcript_path": "/x",
         "from_model": "claude-opus-5-5", "to_model": to, "requested_model": "sonnet", "source": "command",
         "context_tokens": ctx, "prompt_cache_warm": True, "cache_ttl": "1h",
         "estimated_cache_write_usd": 1.42, "pricing": "default"}
    d.update(kw)
    return d


# ---------- fail-open / output discipline ----------

@pytest.mark.parametrize("payload", ["", "not json", "[1,2]", "null", '{"hook_event_name": 7}',
                                     '{"hook_event_name": "SessionStart", "source": 5, "cwd": 3}',
                                     '{"hook_event_name": "UserPromptSubmit", "session_id": "../x", "transcript_path": 1}',
                                     '{"hook_event_name": "PreModelSwitch", "context_tokens": "lots"}',
                                     '{"hook_event_name": "Stop"}', '{"hook_event_name": "SessionEnd"}'])
def test_garbage_input_is_silent(payload, env):
    assert run(payload, env) is None


def test_runs_through_run_sh(env, tmp_path):
    out = run(ss("startup", tmp_path / "repo"), env, via_sh=True)
    assert out is None
    assert "export NO_COLOR=1" in open(env["CLAUDE_ENV_FILE"]).read()


# ---------- (a) quiet env ----------

def test_quiet_env_exports_and_respects_existing(env, tmp_path):
    env["PAGER"] = "less"
    assert run(ss("startup", tmp_path / "repo"), env) is None
    text = open(env["CLAUDE_ENV_FILE"]).read()
    assert "export NO_COLOR=1\n" in text and "export GIT_PAGER=cat\n" in text
    assert "export PAGER=" not in text  # user's own value wins
    assert "FORCE_COLOR" not in text  # rich treats FORCE_COLOR=0 as a terminal and animates progress bars
    assert "CI=" not in text and "PYTEST_ADDOPTS" not in text
    for line in text.strip().splitlines():
        assert line.startswith("export ") and "=" in line


def test_quiet_env_idempotent_and_appends(env, tmp_path):
    with open(env["CLAUDE_ENV_FILE"], "w") as f:
        f.write("export FOO=bar")  # another writer, no trailing newline
    run(ss("startup", tmp_path / "repo"), env)
    run(ss("compact", tmp_path / "repo"), env)
    lines = open(env["CLAUDE_ENV_FILE"]).read().splitlines()
    assert lines[0] == "export FOO=bar"
    assert lines.count("export NO_COLOR=1") == 1


def test_quiet_env_aggressive_and_off(env, tmp_path):
    env["HETZ_QUIET_ENV"] = "aggressive"
    run(ss("startup", tmp_path / "repo"), env)
    text = open(env["CLAUDE_ENV_FILE"]).read()
    assert "export CI=1" in text and "export PYTEST_ADDOPTS='-q -rfE'" in text
    os.remove(env["CLAUDE_ENV_FILE"])
    env["HETZ_QUIET_ENV"] = "off"
    run(ss("startup", tmp_path / "repo"), env)
    assert not os.path.exists(env["CLAUDE_ENV_FILE"])


def test_quiet_env_reaches_bash_when_sourced(env, tmp_path):
    env["HETZ_QUIET_ENV"] = "aggressive"
    run(ss("startup", tmp_path / "repo"), env)
    p = subprocess.run(["bash", "-c", f'. "{env["CLAUDE_ENV_FILE"]}"; echo "$GIT_PAGER|$PYTEST_ADDOPTS"'],
                       capture_output=True, text=True, env=env)
    assert p.stdout.strip() == "cat|-q -rfE"


def test_no_env_file_is_fine(env, tmp_path):
    env.pop("CLAUDE_ENV_FILE")
    assert run(ss("startup", tmp_path / "repo"), env) is None


# ---------- (b) handoff pickup ----------

def make_handoff(repo, text, age_s=0):
    d = repo / ".claude"
    d.mkdir(exist_ok=True)
    p = d / "handoff.md"
    p.write_text(text)
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


def test_handoff_pickup_once(env, tmp_path):
    repo = tmp_path / "repo"
    make_handoff(repo, "# Handoff\nGoal: ship the \"parser\" rewrite\n## Done\n- a\n", age_s=25 * 60)
    out = run(ss("startup", repo), env)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "systemMessage" not in out
    assert ".claude/handoff.md" in ctx and "25 min ago" in ctx and "ship the 'parser' rewrite" in ctx
    assert len(ctx.split()) < 45
    assert run(ss("startup", repo), env) is None  # consumed
    assert run(ss("clear", repo), env) is None


def test_handoff_new_content_is_offered_again(env, tmp_path):
    repo = tmp_path / "repo"
    make_handoff(repo, "Goal: one\n")
    assert run(ss("clear", repo), env)
    make_handoff(repo, "Goal: two\n")
    out = run(ss("clear", repo), env)
    assert 'goal: "two"' in out["hookSpecificOutput"]["additionalContext"]


def test_handoff_goal_heading_form(env, tmp_path):
    repo = tmp_path / "repo"
    make_handoff(repo, "## Goal\n\n- Fix flaky login test\n")
    out = run(ss("startup", repo), env)
    assert 'goal: "Fix flaky login test"' in out["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("source", ["resume", "compact", "fork"])
def test_handoff_only_on_startup_or_clear(env, tmp_path, source):
    repo = tmp_path / "repo"
    make_handoff(repo, "Goal: x\n")
    out = run(ss(source, repo), env)
    assert not out or "hookSpecificOutput" not in out


def test_handoff_stale_or_empty_ignored(env, tmp_path):
    repo = tmp_path / "repo"
    make_handoff(repo, "Goal: old\n", age_s=13 * 3600)
    assert run(ss("startup", repo), env) is None
    make_handoff(repo, "  \n")
    assert run(ss("startup", repo), env) is None


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})


def test_handoff_tracked_team_note_skipped(env, tmp_path):
    repo = tmp_path / "repo"
    git(repo, "init", "-q")
    make_handoff(repo, "# team note\nGoal: shared plan\n")
    assert run(ss("startup", repo), env)  # untracked (personal) note: offered
    make_handoff(repo, "# team note\nGoal: shared plan v2\n")
    git(repo, "add", "-f", ".claude/handoff.md")
    assert run(ss("startup", repo), env) is None  # tracked: fresh mtime after a pull is not a handoff


def test_handoff_still_offered_with_guard_off(env, tmp_path):
    env["HETZ_GUARD"] = "0"
    repo = tmp_path / "repo"
    make_handoff(repo, "Goal: x\n")
    assert run(ss("startup", repo), env)["hookSpecificOutput"]


# ---------- (c) cold resume ----------

def test_cold_resume_warns_with_cost(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 182_000, ttl="1h")
    out = run(ss("resume", tmp_path / "repo", transcript_path=t, model="claude-opus-5-5",
                 prompt_cache_likely_expired=True, context_tokens=182_000, estimated_cache_write_usd=1.82), env)
    msg = out["systemMessage"]
    assert "hookSpecificOutput" not in out
    assert msg.startswith("hetz:") and "182k" in msg and "~$1.46" in msg  # 182k x $8/M (1h write)
    assert "/clear (free)" in msg


def test_cold_resume_mentions_handoff_and_range_when_ttl_unknown(env, tmp_path):
    repo = tmp_path / "repo"
    make_handoff(repo, "Goal: x\n")
    out = run(ss("resume", repo, model="claude-opus-5-5", prompt_cache_likely_expired=True,
                 context_tokens=182_000), env)
    assert "~$0.91–1.46" in out["systemMessage"] and ".claude/handoff.md" in out["systemMessage"]


def test_cold_resume_unknown_model_uses_input_estimate(env, tmp_path):
    out = run(ss("resume", tmp_path / "repo", model="mystery-model", prompt_cache_likely_expired=True,
                 context_tokens=90_000, estimated_cache_write_usd=0.5625), env)
    assert "~$0.56" in out["systemMessage"]


@pytest.mark.parametrize("kw", [
    {"prompt_cache_likely_expired": False, "context_tokens": 182_000},
    {"prompt_cache_likely_expired": True, "context_tokens": 59_000},
])
def test_cold_resume_quiet_cases(env, tmp_path, kw):
    assert run(ss("resume", tmp_path / "repo", model="claude-opus-5-5", **kw), env) is None


def test_cold_resume_guard_off(env, tmp_path):
    env["HETZ_GUARD"] = "off"
    assert run(ss("resume", tmp_path / "repo", prompt_cache_likely_expired=True, context_tokens=182_000), env) is None


def test_cold_resume_fallback_on_transcript_age(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 120_000, model="claude-sonnet-5-5", ttl="5m")
    old = time.time() - 600
    os.utime(t, (old, old))
    out = run(ss("resume", tmp_path / "repo", transcript_path=t), env)
    assert "120k" in out["systemMessage"] and "~$0.30" in out["systemMessage"]  # 120k x $2.50/M
    os.utime(t, None)  # just touched: cache still warm
    assert run(ss("resume", tmp_path / "repo", transcript_path=t), env) is None


# ---------- (d) size and commit-boundary nudges ----------

def test_size_nudges_once_each_and_capped(env, tmp_path):
    t = tmp_path / "t.jsonl"
    write_transcript(t, 90_000)
    assert run(ups(str(t)), env) is None
    write_transcript(t, 104_000)
    msg = run(ups(str(t)), env)["systemMessage"]
    assert "104k" in msg and "/req on Opus 5.5" in msg and "~$0.02" in msg and "/hetz:handoff" in msg
    assert run(ups(str(t)), env) is None  # 100k already announced
    write_transcript(t, 152_000)
    assert "152k" in run(ups(str(t)), env)["systemMessage"]
    assert run(ups(str(t)), env) is None


def test_both_thresholds_crossed_at_once_gives_one_message(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 160_000)
    assert run(ups(t), env)["systemMessage"]
    assert run(ups(t), env) is None


def test_commit_flag_boundary_nudge(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 120_000, first_ctx=18_000)
    flag = set_commit_flag(env)
    msg = run(ups(t), env)["systemMessage"]
    assert "boundary" in msg and "~120k → ~18k" in msg
    assert "Esc, /clear, re-send" in msg  # the prompt already went out: say how to redo it
    assert not os.path.exists(flag)
    assert run(ups(t), env) is None  # thresholds were folded into the boundary message


def test_commit_flag_small_context_consumed_silently(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 30_000)
    flag = set_commit_flag(env)
    assert run(ups(t), env) is None
    assert not os.path.exists(flag)


def test_message_cap_three_per_session(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 50_000)
    shown = 0
    for i in range(6):
        set_commit_flag(env)
        if run(stop(t) if i % 2 else ups(t), env):
            shown += 1
    assert shown == 3


def test_prompt_skips(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 160_000)
    assert run(ups(t, prompt="/hetz:handoff"), env) is None
    assert run(ups(t, source="loop_wakeup"), env) is None
    env["HETZ_GUARD"] = "0"
    assert run(ups(t), env) is None
    env.pop("HETZ_GUARD")
    assert run(ups(t, source="user"), env)["systemMessage"]


def test_synthetic_last_message_falls_back_to_previous_real_usage(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 160_000)
    append(t, {"type": "assistant", "message": {"role": "assistant", "model": "<synthetic>",
                                                "usage": {"input_tokens": 0, "output_tokens": 0}}})
    flag = set_commit_flag(env)
    msg = run(ups(t), env)["systemMessage"]
    assert "boundary" in msg and "160k" in msg
    assert not os.path.exists(flag)


def test_zero_usage_and_sidechain_records_skipped(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 120_000)
    append(t, assistant(400_000, isSidechain=True),
           {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5", "usage": {}}})
    assert "120k" in run(ups(t), env)["systemMessage"]


def test_compact_rearms_size_nudges_only_after_a_new_reply(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 110_000)
    assert run(ups(t), env)
    assert run(ups(t), env) is None
    append(t, boundary(post=9_000), SUMMARY)  # /compact: no assistant usage until the next reply
    run(ss("compact", tmp_path / "repo"), env)
    assert run(ups(t), env) is None  # the pre-compaction 110k is stale
    append(t, assistant(30_000))
    assert run(ups(t), env) is None
    append(t, assistant(104_000))
    assert "104k" in run(ups(t), env)["systemMessage"]


@pytest.mark.parametrize("post", [9_000, None])
def test_commit_right_after_compaction_is_silent(env, tmp_path, post):
    t = write_transcript(tmp_path / "t.jsonl", 160_000)
    append(t, boundary(post=post), SUMMARY)
    flag = set_commit_flag(env)
    assert run(ups(t), env) is None
    assert not os.path.exists(flag)


def test_large_post_compaction_size_counts(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 190_000)
    append(t, boundary(post=120_000))
    set_commit_flag(env)
    assert "~120k" in run(ups(t), env)["systemMessage"]


def test_tail_state(tmp_path):
    g = load_guard()
    assert g.tail_state(None) == (None, 0) and g.tail_state("/nonexistent") == (None, 0)
    t = write_transcript(tmp_path / "t.jsonl", 150_000, model="claude-sonnet-5-5")
    usage, ctx = g.tail_state(t)
    assert ctx == 150_000 and usage["model"] == "claude-sonnet-5-5"
    append(t, assistant(50_000, message={"role": "assistant", "content": "mentions \"compact_boundary\""}))
    assert g.tail_state(t)[1] == 150_000  # text mentioning compact_boundary is not a boundary
    append(t, boundary(post=12_345))
    assert g.tail_state(t) == (None, 12_345)


# ---------- (d') Stop: same nudges at the end of the turn ----------

def test_stop_commit_boundary_before_next_prompt(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 120_000, first_ctx=18_000)
    flag = set_commit_flag(env)
    out = run(stop(t), env)
    assert set(out) == {"systemMessage"}  # never decision/additionalContext: those continue the turn
    assert "Next task: /clear first" in out["systemMessage"] and "~120k → ~18k" in out["systemMessage"]
    assert not os.path.exists(flag)
    assert run(ups(t), env) is None  # already said at Stop


def test_stop_size_nudge_then_prompt_is_quiet(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 104_000)
    assert "104k" in run(stop(t), env)["systemMessage"]
    assert run(ups(t), env) is None
    assert run(stop(t), env) is None


def test_stop_quiet_cases(env, tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", 90_000)
    assert run(stop(t), env) is None
    write_transcript(tmp_path / "t.jsonl", 160_000)
    env["HETZ_GUARD"] = "0"
    assert run(stop(t), env) is None
    assert run(stop(t, session_id="../x"), env) is None


# ---------- (e) model switch ----------

def test_model_switch_warns(env):
    out = run(pms(), env)
    msg = out["systemMessage"]
    assert set(out) == {"systemMessage"}  # never blocks or asks
    assert "Sonnet 5.5" in msg and "142k" in msg and "~$0.57" in msg  # 142k x $4/M (1h write)
    assert "/hetz:handoff" in msg


def test_model_switch_dedupes_double_fire(env):
    assert run(pms(), env)
    assert run(pms(), env) is None


@pytest.mark.parametrize("kw", [{"ctx": 20_000}, {"prompt_cache_warm": False}])
def test_model_switch_quiet_cases(env, kw):
    assert run(pms(**kw), env) is None


def test_model_switch_guard_off(env):
    env["HETZ_GUARD"] = "no"
    assert run(pms(), env) is None


def test_model_switch_configured_pricing_and_unknown_model(env):
    assert "~$9.99" in run(pms(pricing="configured", estimated_cache_write_usd=9.99), env)["systemMessage"]
    out = run(pms(to="acme-model", session_id="other", estimated_cache_write_usd=0.71), env)
    assert "~$0.71" in out["systemMessage"]


def test_model_switch_ttl_unknown_gives_range(env):
    out = run(pms(to="claude-opus-5-5[1m]", cache_ttl=None, estimated_cache_write_usd=None), env)
    assert "Opus 5.5" in out["systemMessage"] and "~$0.71–1.14" in out["systemMessage"]


# ---------- prune ----------

def test_prune_old_logs_and_state(env, tmp_path):
    data = tmp_path / "data"
    old_dir = data / "logs" / "old-session"
    old_dir.mkdir(parents=True)
    (old_dir / "a.log").write_text("x")
    fresh = data / "logs" / "new-session"
    fresh.mkdir(parents=True)
    (fresh / "b.log").write_text("y")
    (data / "state").mkdir(parents=True)
    stale = data / "state" / "old.json"
    stale.write_text("{}")
    t = time.time() - 8 * 86400
    for p in (old_dir / "a.log", stale):
        os.utime(p, (t, t))
    (data / "squeeze.tsv").write_text("ledger\n")
    os.utime(data / "squeeze.tsv", (t, t))
    run(ss("startup", tmp_path / "repo"), env)
    assert not old_dir.exists() and not stale.exists()
    assert (fresh / "b.log").exists() and (data / "squeeze.tsv").exists()


# ---------- prices ----------

def test_prices_schema():
    prices = json.load(open(os.path.join(HOOKS, "prices.json")))
    assert prices["_source"].startswith("https://") and prices["_date"] == "2026-10-17"
    keys = {"input", "cache_write_5m", "cache_write_1h", "cache_read", "output"}
    for name, row in list(prices["models"].items()) + list(prices["_over_100k"].items()):
        assert name.startswith("claude-") and set(row) == keys, name
        assert all(isinstance(v, (int, float)) and v > 0 for v in row.values()), name
    assert prices["models"]["claude-opus-5-5"]["cache_read"] == 0.2
    assert prices["models"]["claude-sonnet-5-5"]["output"] == 10
    assert prices["models"]["claude-haiku-5-5"]["input"] == 0.1


@pytest.mark.parametrize("model,ctx,field,expected", [
    ("claude-opus-5-5", 0, "input", 4.0),
    ("claude-opus-5-5[1m]", 0, "cache_write_1h", 8.0),
    ("claude-opus-5", 0, "input", 5.0),
    ("claude-opus-4-20250514", 0, "input", 15.0),
    ("claude-opus-4-5-20251101", 0, "input", 5.0),
    ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", 0, "input", 3.0),
    ("claude-sonnet-5", 0, "cache_read", 0.2),
    ("claude-haiku-5-5", 50_000, "input", 0.1),
    ("claude-haiku-5-5", 150_000, "input", 0.5),
    ("claude-fable-5-1", 0, "cache_read", 0.25),
])
def test_price_lookup_longest_prefix(model, ctx, field, expected):
    g = load_guard()
    assert g.price_for(model, ctx)[field] == expected


def test_price_lookup_unknown():
    g = load_guard()
    assert g.price_for("gpt-9") is None and g.price_for(None) is None


@pytest.mark.parametrize("model,label", [("claude-opus-5-5", "Opus 5.5"), ("claude-sonnet-5", "Sonnet 5"),
                                         ("claude-opus-4-20250514", "Opus 4"), ("claude-haiku-4-5-20251001", "Haiku 4.5")])
def test_model_label(model, label):
    assert load_guard().model_label(model) == label
