"""Tests for scripts/statusline.py with sample statusLine JSON (fields as sent by Claude Code 2.1.272)."""
import copy
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

import pytest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SCRIPTS, "statusline.py")
SEP = " │ "
PY = os.path.realpath(sys.executable)

BASE = {
    "session_id": "s1",
    "transcript_path": "/nonexistent/t.jsonl",
    "cwd": "/nonexistent",
    "model": {"id": "claude-sonnet-5-5", "display_name": "Sonnet 5.5"},
    "workspace": {"current_dir": "/nonexistent", "project_dir": "/nonexistent"},
    "version": "2.1.272",
    "output_style": {"name": "Concise"},
    "cost": {"total_cost_usd": 3.1234, "total_duration_ms": 1000},
    "context_window": {
        "total_input_tokens": 142000,
        "total_output_tokens": 500,
        "context_window_size": 1000000,
        "current_usage": {"input_tokens": 2000, "output_tokens": 500, "cache_creation_input_tokens": 10000,
                          "cache_read_input_tokens": 130000},
        "used_percentage": 14.2,
        "remaining_percentage": 85.8,
    },
    "exceeds_200k_tokens": False,
    "prompt_cache": {"warm": True, "caching_observed": True, "ttl": "1h", "expires_at": 1890000000, "requests": 12,
                     "misses": 1, "hit_ratio": 0.962},
    "effort": {"level": "medium"},
    "thinking": {"enabled": True},
}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / ".claude" / "settings.json").write_text(json.dumps({"autoCompactWindow": 200000}))
    return h


def sl(payload, home, *args, color=False, extra_env=None, script=SCRIPT):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "HETZ", "NO_COLOR"))}
    env["HETZ_HOME"] = str(home)
    if not color:
        env["NO_COLOR"] = "1"
    env.update(extra_env or {})
    raw = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    proc = subprocess.run([PY, "-I", script, *args], input=raw if isinstance(raw, bytes) else raw.encode(),
                          capture_output=True, env=env, timeout=30)
    assert proc.returncode == 0
    assert proc.stderr == b""
    return proc.stdout.decode()


def with_tokens(total, **over):
    d = copy.deepcopy(BASE)
    d["context_window"]["current_usage"] = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": total}
    d.update(over)
    return d


def test_design_line(home):
    assert sl(BASE, home) == "sonnet·med" + SEP + "ctx 142k/200k" + SEP + "cache 96% warm" + SEP + "$3.12\n"


def test_window_is_min_of_compact_window_and_model_window(home):
    d = copy.deepcopy(BASE)
    d["context_window"]["context_window_size"] = 150000
    assert "ctx 142k/150k" in sl(d, home)


def test_window_sources(home, tmp_path):
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    d = copy.deepcopy(BASE)
    d["workspace"]["project_dir"] = str(proj)
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({"autoCompactWindow": 300000}))
    assert "/300k" in sl(d, home)
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({"autoCompactWindow": 5}))  # invalid -> ignored
    assert "/200k" in sl(d, home)
    assert "/250k" in sl(d, home, extra_env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "250000"})
    (home / ".claude" / "settings.json").write_text("{broken")
    assert "/1M" in sl(d, home)


@pytest.mark.parametrize("tokens,code,suffix", [(50000, "2", False), (80000, "33", False), (149000, "33", False), (151000, "31", True)])
def test_ctx_colors(home, tokens, code, suffix):
    out = sl(with_tokens(tokens), home, color=True)
    assert "\033[%sm" % code in out
    assert ("/hetz:handoff + /clear" in out) == suffix


def test_no_color_flag(home):
    out = sl(with_tokens(151000), home, "--no-color", color=True)
    assert "\033[" not in out and "→ /hetz:handoff + /clear" in out


def test_cache_states(home):
    d = copy.deepcopy(BASE)
    d["prompt_cache"]["warm"] = False
    out = sl(d, home, color=True)
    assert "cache COLD" in out and "\033[31mcache COLD" in out
    d["context_window"]["current_usage"] = {"input_tokens": 1000}
    assert "\033[33mcache COLD" in sl(d, home, color=True)
    d = copy.deepcopy(BASE)
    d["prompt_cache"].update(hit_ratio=0.5, requests=3)
    assert "\033[31mcache 50% warm" in sl(d, home, color=True)
    d["prompt_cache"]["requests"] = 2
    out = sl(d, home, color=True)
    assert "cache 50% warm" in out and "\033[31mcache" not in out
    d["prompt_cache"]["hit_ratio"] = None
    assert "cache warm" in sl(d, home)


def test_cache_fallback_without_prompt_cache(home):
    d = copy.deepcopy(BASE)
    del d["prompt_cache"]
    out = sl(d, home)
    assert "cache 92%" in out  # 130000 / 142000


@pytest.mark.parametrize("model,label", [
    ({"id": "claude-opus-5-5[1m]", "display_name": "Opus 5.5"}, "opus"),
    ({"id": "claude-haiku-5-5"}, "haiku"),
    ({"id": "claude-fable-5"}, "fable"),
    ({"display_name": "Custom Thing"}, "custom"),
    ("claude-sonnet-5-5", "sonnet"),
])
def test_model_labels(home, model, label):
    d = copy.deepcopy(BASE)
    d["model"] = model
    del d["effort"]
    assert sl(d, home).startswith(label + SEP)


def test_effort_labels(home):
    d = copy.deepcopy(BASE)
    d["effort"] = {"level": "xhigh"}
    assert sl(d, home).startswith("sonnet·xhi" + SEP)
    d["effort"] = {"level": 7}
    assert sl(d, home).startswith("sonnet·7" + SEP)


@pytest.mark.parametrize("payload,expected", [
    (b"", "\n"),
    (b"garbage", "\n"),
    (b"[1,2,3]", "\n"),
    (b"\xff\xfe\x00", "\n"),
    (json.dumps({"model": {"id": "claude-opus-5-5"}}), "opus\n"),
    (json.dumps({"model": {"id": "claude-opus-5-5"}, "context_window": "nope", "cost": {"total_cost_usd": "x"}}), "opus\n"),
    (json.dumps({"model": {"id": "claude-haiku-5-5"}, "context_window": {"current_usage": None, "total_input_tokens": 0,
                                                                          "context_window_size": 200000}}),
     "haiku" + SEP + "ctx 0/200k\n"),
])
def test_bad_or_partial_input_never_errors(home, payload, expected):
    assert sl(payload, home) == expected


def test_weird_types_do_not_crash(home):
    d = copy.deepcopy(BASE)
    d["context_window"]["current_usage"] = {"input_tokens": "1000", "cache_read_input_tokens": True, "cache_creation_input_tokens": None}
    d["prompt_cache"] = {"warm": "yes", "hit_ratio": True, "requests": "many"}
    d["cost"] = {"total_cost_usd": None}
    d["effort"] = "high"
    d["model"] = {"id": 5}
    out = sl(d, home)
    assert out == "5" + SEP + "ctx 1k/200k" + SEP + "cache warm\n"


def test_cost_formatting(home):
    d = copy.deepcopy(BASE)
    d["cost"]["total_cost_usd"] = 0
    assert sl(d, home).endswith("$0.00\n")
    d["cost"]["total_cost_usd"] = 123.456
    assert sl(d, home).endswith("$123.46\n")


def test_large_contexts_format(home):
    d = with_tokens(1234567)
    (home / ".claude" / "settings.json").write_text("{}")
    assert "ctx 1.2M/1M" in sl(d, home)


def _install_copy(tmp_path, command):
    hetz = tmp_path / "hetzdir"
    hetz.mkdir()
    shutil.copy(SCRIPT, hetz / "statusline.py")
    if command is not None:
        (hetz / "statusline-wrap.json").write_text(json.dumps({"command": command}))
    return str(hetz / "statusline.py")


def test_wrap_appends_to_user_line(home, tmp_path):
    script = _install_copy(tmp_path, "read x; echo user-line")
    out = sl(BASE, home, "--wrap", script=script)
    assert out == "user-line" + SEP + "sonnet·med" + SEP + "ctx 142k/200k" + SEP + "cache 96% warm" + SEP + "$3.12\n"


def test_wrap_multiline_and_failures(home, tmp_path):
    script = _install_copy(tmp_path, "printf 'one\\ntwo\\n'")
    out = sl({"model": {"id": "claude-opus-5-5"}}, home, "--wrap", script=script)
    assert out == "one" + SEP + "opus\ntwo\n"
    bad = tmp_path / "bad"
    bad.mkdir()
    shutil.copy(SCRIPT, bad / "statusline.py")
    (bad / "statusline-wrap.json").write_text(json.dumps({"command": "exit 3"}))
    assert sl({"model": {"id": "claude-opus-5-5"}}, home, "--wrap", script=str(bad / "statusline.py")) == "opus\n"
    nocfg = tmp_path / "nocfg"
    nocfg.mkdir()
    shutil.copy(SCRIPT, nocfg / "statusline.py")
    assert sl({"model": {"id": "claude-opus-5-5"}}, home, "--wrap", script=str(nocfg / "statusline.py")) == "opus\n"


def test_wrap_passes_stdin_to_user_command(home, tmp_path):
    script = _install_copy(tmp_path, "python3 -c 'import json,sys; print(json.load(sys.stdin)[\"session_id\"])'")
    assert sl(BASE, home, "--wrap", script=script).startswith("s1" + SEP)


def test_fast_enough(home):
    raw = json.dumps(BASE).encode()
    env = dict(os.environ, HETZ_HOME=str(home), NO_COLOR="1")
    times = []
    for _ in range(7):
        t = time.perf_counter()
        subprocess.run([PY, "-I", SCRIPT], input=raw, capture_output=True, env=env)
        times.append((time.perf_counter() - t) * 1000)
    # Design budget is <50 ms of script work; allow interpreter start-up noise on loaded CI machines.
    assert statistics.median(times) < 150, times


def test_wrap_never_recurses_into_itself(home, tmp_path):
    hetz = tmp_path / "loop"
    hetz.mkdir()
    shutil.copy(SCRIPT, hetz / "statusline.py")
    self_cmd = "%s -I %s --wrap" % (PY, hetz / "statusline.py")
    (hetz / "statusline-wrap.json").write_text(json.dumps({"command": self_cmd}))
    out = sl({"model": {"id": "claude-opus-5-5"}}, home, "--wrap", script=str(hetz / "statusline.py"))
    assert out == "opus" + SEP + "opus\n"


def test_non_utf8_locale_still_prints(home):
    out = sl(BASE, home, extra_env={"LC_ALL": "en_US.ISO8859-1", "LANG": "en_US.ISO8859-1"})
    assert out == "sonnet·med" + SEP + "ctx 142k/200k" + SEP + "cache 96% warm" + SEP + "$3.12\n"
