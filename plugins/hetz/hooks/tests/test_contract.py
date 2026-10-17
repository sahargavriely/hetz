"""Tests for hooks/contract.py (SubagentStart return contract). Self-contained: no conftest."""
import json
import os
import subprocess
import sys

import pytest

HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(HOOKS, "contract.py")
RUN_SH = os.path.join(HOOKS, "run.sh")


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("HETZ_", "CLAUDE_"))}
    e["HOME"] = str(tmp_path)
    e["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "data")
    return e


def run(payload, env, via_sh=False):
    data = payload if isinstance(payload, str) else json.dumps(payload)
    cmd = ["sh", RUN_SH, "contract.py"] if via_sh else [sys.executable, "-I", CONTRACT]
    p = subprocess.run(cmd, input=data.encode(), env=env, capture_output=True, timeout=20)
    assert p.returncode == 0, p.stderr
    out = p.stdout.decode()
    return json.loads(out) if out else None


def start(agent_type, cwd="/nonexistent-hetz-cwd"):
    return {"hook_event_name": "SubagentStart", "session_id": "s", "transcript_path": "/x", "cwd": cwd,
            "agent_id": "a1", "agent_type": agent_type}


def agent_file(base, name):
    d = base / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / (name + ".md")).write_text("---\nname: %s\n---\nReturn exactly: Answer / Evidence\n" % name)


@pytest.mark.parametrize("agent_type", ["Explore", "general-purpose"])
def test_contract_for_builtin_agents(env, agent_type):
    out = run(start(agent_type), env)
    hso = out["hookSpecificOutput"]
    assert set(out) == {"hookSpecificOutput"}
    assert hso["hookEventName"] == "SubagentStart"
    ctx = hso["additionalContext"]
    assert "path:line" in ctx and "25 lines" in ctx and "not checked" in ctx
    assert len(ctx.split()) <= 45  # ~35-40 tokens per spawn


@pytest.mark.parametrize("agent_type", ["Plan", "hetz-crew:runner", "hetz-crew:judge", "Explorer",
                                        "my-general-purpose", "", None, 3])
def test_no_contract_for_other_agents(env, agent_type):
    assert run(start(agent_type), env) is None


def test_user_override_keeps_its_own_format(env, tmp_path):
    agent_file(tmp_path / ".claude", "Explore")  # e.g. hetz's templates/Explore.md installed by /hetz:setup
    assert run(start("Explore"), env) is None
    assert run(start("general-purpose"), env)["hookSpecificOutput"]


def test_config_dir_override(env, tmp_path):
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "cfg")
    agent_file(tmp_path / ".claude", "Explore")  # ignored: the config dir moved
    assert run(start("Explore"), env)
    agent_file(tmp_path / "cfg", "general-purpose")
    assert run(start("general-purpose"), env) is None


def test_project_override_found_from_subdir(env, tmp_path):
    repo = tmp_path / "work" / "repo"
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    agent_file(repo / ".claude", "Explore")
    assert run(start("Explore", str(sub)), env) is None
    assert run(start("Explore", str(tmp_path / "work")), env)
    env["CLAUDE_PROJECT_DIR"] = str(repo)
    assert run(start("Explore", str(tmp_path / "work")), env) is None


@pytest.mark.parametrize("cwd", [5, None, "relative/dir", ""])
def test_odd_cwd_still_gets_contract(env, cwd):
    assert run(start("Explore", cwd), env)["hookSpecificOutput"]


def test_wrong_event_ignored(env):
    payload = start("Explore")
    payload["hook_event_name"] = "SubagentStop"
    assert run(payload, env) is None


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "OFF"])
def test_switch_off(env, value):
    env["HETZ_CONTRACT"] = value
    assert run(start("Explore"), env) is None


@pytest.mark.parametrize("payload", ["", "garbage", "[]", "null", "{\"agent_type\": {}}"])
def test_garbage_is_silent(env, payload):
    assert run(payload, env) is None


def test_runs_through_run_sh(env):
    out = run(start("Explore"), env, via_sh=True)
    assert out["hookSpecificOutput"]["additionalContext"]
