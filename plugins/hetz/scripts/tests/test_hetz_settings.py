"""Tests for scripts/hetz_settings.py. Every test uses a temp home (HETZ_HOME / --home); the real ~/.claude is never touched."""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.dirname(SCRIPTS)
SCRIPT = os.path.join(SCRIPTS, "hetz_settings.py")
EXPLORE_TEMPLATE = os.path.join(PLUGIN, "templates", "Explore.md")

_spec = importlib.util.spec_from_file_location("hetz_settings_under_test", SCRIPT)
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)

SAMPLE_STATUS_JSON = {
    "model": {"id": "claude-sonnet-5-5", "display_name": "Sonnet 5.5"},
    "effort": {"level": "medium"},
    "context_window": {"context_window_size": 1000000, "total_input_tokens": 142000,
                       "current_usage": {"input_tokens": 2000, "cache_read_input_tokens": 130000, "cache_creation_input_tokens": 10000}},
    "prompt_cache": {"warm": True, "hit_ratio": 0.962, "requests": 12},
    "cost": {"total_cost_usd": 3.1234},
}


# ---------------------------------------------------------------- harness

@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC", "HETZ", "XDG"))}
    e["HETZ_HOME"] = str(home)
    # No real `claude` on PATH: status must not depend on it, and nothing can reach the real install.
    e["PATH"] = os.pathsep.join([str(bin_dir), os.path.dirname(os.path.realpath(sys.executable)), "/usr/bin", "/bin"])
    e.pop("NO_COLOR", None)
    for k in [k for k in os.environ if k.startswith(("CLAUDE", "ANTHROPIC", "HETZ", "XDG"))]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HETZ_HOME", str(home))
    monkeypatch.setenv("PATH", e["PATH"])
    return {"home": home, "proj": proj, "bin": bin_dir, "env": e, "tmp": tmp_path}


def run(env, *args, ok=True, cwd=None, extra_env=None):
    e = dict(env["env"])
    e.update(extra_env or {})
    proc = subprocess.run([sys.executable, "-I", SCRIPT, "--shell", "fish", *args], cwd=str(cwd or env["proj"]),
                          env=e, capture_output=True, text=True, timeout=60)
    if ok:
        assert proc.returncode == 0, proc.stderr + proc.stdout
    return proc


def run_json(env, *args, **kw):
    return json.loads(run(env, *args, "--json", **kw).stdout)


def user_settings_path(env):
    return env["home"] / ".claude" / "settings.json"


def write_user(env, data):
    p = user_settings_path(env)
    p.write_text(json.dumps(data, indent=4))
    return p


def read_user(env):
    return json.loads(user_settings_path(env).read_text())


def manifest(env):
    p = env["home"] / ".claude" / "hetz" / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else None


def tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


ALL_GLOBAL = "cap,output-caps,env,glob-deny,concise,statusline,explore,launchers,model-sonnet,read-cap,goal-checkin"


# ---------------------------------------------------------------- CLI plumbing

def test_home_option_before_and_after_subcommand(env, tmp_path):
    other = tmp_path / "other"
    (other / ".claude").mkdir(parents=True)
    e = dict(env["env"])
    e.pop("HETZ_HOME")
    for argv in (["--home", str(other), "status", "--json"], ["status", "--home", str(other), "--json"]):
        proc = subprocess.run([sys.executable, "-I", SCRIPT, *argv], env=e, capture_output=True, text=True, cwd=str(env["proj"]))
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["home"] == str(other)


def test_hetz_home_env_is_used(env):
    st = run_json(env, "status")
    assert st["home"] == str(env["home"])
    assert st["settings"]["user"]["file"] == str(user_settings_path(env))


def test_unknown_item_and_empty_request_fail_cleanly(env):
    p = run(env, "apply", "--items", "nope", ok=False)
    assert p.returncode == 1 and "unknown item" in p.stderr
    p = run(env, "apply", ok=False)
    assert p.returncode == 1 and "nothing to apply" in p.stderr


def test_invalid_json_settings_aborts_without_writing(env):
    p = user_settings_path(env)
    p.write_text("{ not json")
    proc = run(env, "apply", "--items", "cap", ok=False)
    assert proc.returncode == 1 and "not valid JSON" in proc.stderr
    assert p.read_text() == "{ not json"
    assert manifest(env) is None


# ---------------------------------------------------------------- status

def test_status_empty_home_offers_valid_question_groups(env):
    st = run_json(env, "status")
    assert st["claude_version"] is None and st["version_ok"] is False
    assert not st["settings"]["user"]["exists"]
    assert 1 <= len(st["ask"]) <= 4
    for group in st["ask"]:
        assert 2 <= len(group["items"]) <= 4  # AskUserQuestion: 2-4 options per question
    offered = [i for g in st["ask"] for i in g["items"]]
    assert "statusline" in offered and "statusline-wrap" not in offered
    assert "advisor" not in offered  # API-auth only
    assert st["items"]["cap"]["recommended"] and not st["items"]["model-sonnet"]["recommended"]
    text = run(env, "status").stdout
    assert "ask:" in text and "cap*" in text and "Core:" in text


def test_status_reports_conflicts(env):
    write_user(env, {"outputStyle": "Explanatory",
                     "statusLine": {"type": "command", "command": "~/my-line.sh"},
                     "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "rtk hook claude"}]}]},
                     "enabledPlugins": {"caveman@caveman": True}})
    st = run_json(env, "status")
    notes = "\n".join(st["notes"])
    assert "outputStyle" in notes and "statusline-wrap" in notes and "rtk" in notes and "caveman" in notes
    offered = [i for g in st["ask"] for i in g["items"]]
    assert "statusline-wrap" in offered and "statusline" not in offered
    assert st["items"]["concise"]["state"] == "conflict"


def test_status_advisor_only_on_api_auth(env):
    st = run_json(env, "status", extra_env={"ANTHROPIC_API_KEY": "test-not-a-key"})
    assert st["auth"] == "api"
    assert "advisor" in st["items"]
    st = run_json(env, "status", "--auth", "subscription")
    assert "advisor" not in st["items"]


def test_status_detects_subscription_and_claude_version(env):
    (env["home"] / ".claude.json").write_text(json.dumps({"oauthAccount": {"x": 1}}))
    stub = env["bin"] / "claude"
    stub.write_text("#!/bin/sh\necho '2.1.272 (Claude Code)'\n")
    stub.chmod(0o755)
    st = run_json(env, "status")
    assert st["auth"] == "subscription"
    assert st["claude_version"] == "2.1.272" and st["version_ok"]
    stub.write_text("#!/bin/sh\necho '2.1.100 (Claude Code)'\n")
    st = run_json(env, "status")
    assert not st["version_ok"] and any("older" in n for n in st["notes"])


# ---------------------------------------------------------------- apply / dry-run

def test_dry_run_writes_nothing(env):
    original = {"theme": "dark", "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "opus"}}
    write_user(env, original)
    before = tree(env["home"])
    out = run(env, "apply", "--items", ALL_GLOBAL, "--dry-run").stdout
    assert "Nothing written (dry run)." in out
    assert "+ autoCompactWindow = 200000" in out
    assert "! env.CLAUDE_CODE_SUBAGENT_MODEL" in out and "--force-keys env.CLAUDE_CODE_SUBAGENT_MODEL" in out
    assert "+ permissions.deny += \"Read(//**/*.min.js)\"" in out
    assert tree(env["home"]) == before


def test_apply_merges_and_never_overwrites_silently(env):
    original = {"theme": "dark", "outputStyle": "Explanatory",
                "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "opus", "KEEP_ME": "1"},
                "permissions": {"deny": ["Read(//**/*.min.js)", "Bash(rm -rf *)"], "allow": ["Bash(ls)"]}}
    write_user(env, original)
    out = run(env, "apply", "--items", "cap,output-caps,env,glob-deny,concise").stdout
    assert "Takes effect in new sessions." in out
    s = read_user(env)
    assert s["theme"] == "dark" and s["outputStyle"] == "Explanatory"
    assert s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "opus" and s["env"]["KEEP_ME"] == "1"
    assert s["env"]["CLAUDE_CODE_GLOB_NO_IGNORE"] == "false"
    assert s["env"]["MAX_MCP_OUTPUT_TOKENS"] == "12000"
    assert s["env"]["CLAUDE_CODE_IDLE_THRESHOLD_MINUTES"] == "60"
    assert s["autoCompactWindow"] == 200000 and s["bashOutputMaxChars"] == 12000
    deny = s["permissions"]["deny"]
    assert deny.count("Read(//**/*.min.js)") == 1 and "Bash(rm -rf *)" in deny
    assert {"Read(//**/*.min.css)", "Read(//**/*.js.map)", "Read(//**/*.css.map)"} <= set(deny)
    assert s["permissions"]["allow"] == ["Bash(ls)"]
    backups = list((env["home"] / ".claude").glob("settings.json.hetz-*.bak"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original
    m = manifest(env)
    assert len(m["entries"]) == 1 and m["entries"][0]["backup"] == str(backups[0])
    paths_changed = {".".join(op["path"]) for op in m["entries"][0]["ops"]}
    assert "env.CLAUDE_CODE_SUBAGENT_MODEL" not in paths_changed and "outputStyle" not in paths_changed


def test_apply_values_are_valid_for_2_1_272(env):
    run(env, "apply", "--items", "cap,output-caps,concise")
    s = read_user(env)
    assert isinstance(s["autoCompactWindow"], int) and 100000 <= s["autoCompactWindow"] <= 1000000
    assert isinstance(s["bashOutputMaxChars"], int) and 4000 <= s["bashOutputMaxChars"] <= 128000
    assert s["outputStyle"] == "Concise"
    for rule in read_user(env).get("permissions", {}).get("deny", []):
        assert rule.startswith("Read(//")


def test_api_auth_uses_short_idle_threshold(env):
    run(env, "apply", "--items", "env", extra_env={"ANTHROPIC_API_KEY": "test-not-a-key"})
    assert read_user(env)["env"]["CLAUDE_CODE_IDLE_THRESHOLD_MINUTES"] == "5"


def test_apply_is_idempotent(env):
    write_user(env, {"theme": "dark"})
    run(env, "apply", "--items", ALL_GLOBAL)
    snap = tree(env["home"])
    out = run(env, "apply", "--items", ALL_GLOBAL).stdout
    assert "Nothing to change." in out
    assert tree(env["home"]) == snap
    assert len(manifest(env)["entries"]) == 1


def test_force_keys_replace_and_undo_restores_prior(env):
    original = {"outputStyle": "Explanatory", "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "opus"}}
    write_user(env, original)
    run(env, "apply", "--items", "concise,env", "--force-keys", "outputStyle,env.CLAUDE_CODE_SUBAGENT_MODEL")
    s = read_user(env)
    assert s["outputStyle"] == "Concise" and s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "sonnet"
    run(env, "undo", "--scope", "user")
    assert read_user(env) == original


def test_full_apply_then_undo_is_exact(env):
    original = {"theme": "dark", "env": {"FOO": "1"}, "permissions": {"deny": ["Read(./secret)"]}}
    write_user(env, original)
    claude_before = {k: v for k, v in tree(env["home"]).items()}
    run(env, "apply", "--items", ALL_GLOBAL)
    assert (env["home"] / ".claude" / "agents" / "Explore.md").exists()
    assert (env["home"] / ".config" / "fish" / "functions" / "cc.fish").exists()
    assert (env["home"] / ".claude" / "hetz" / "statusline.py").exists()
    assert (env["home"] / ".claude" / "hetz" / "py").exists()
    preview = run(env, "undo", "--scope", "user", "--dry-run").stdout
    assert "Nothing written (dry run)." in preview and "Explore.md" in preview
    assert (env["home"] / ".claude" / "agents" / "Explore.md").exists()
    run(env, "undo", "--scope", "user")
    assert read_user(env) == original
    after = tree(env["home"])
    leftovers = {k for k in after if k not in claude_before and ".hetz-" not in k}
    assert leftovers == set(), leftovers
    assert not (env["home"] / ".config").exists()
    assert not (env["home"] / ".claude" / "agents").exists()
    assert manifest(env) is None


def test_undo_keeps_values_changed_since_apply_unless_forced(env):
    write_user(env, {})
    run(env, "apply", "--items", "cap,output-caps")
    s = read_user(env)
    s["autoCompactWindow"] = 300000
    write_user(env, s)
    out = run(env, "undo", "--scope", "user").stdout
    assert "changed since apply" in out
    s = read_user(env)
    assert s["autoCompactWindow"] == 300000 and "bashOutputMaxChars" not in s
    run(env, "apply", "--items", "output-caps")
    s = read_user(env)
    s["bashOutputMaxChars"] = 20000
    write_user(env, s)
    run(env, "undo", "--scope", "user", "--force")
    assert "bashOutputMaxChars" not in read_user(env)


def test_created_settings_file_is_removed_by_undo(env):
    assert not user_settings_path(env).exists()
    run(env, "apply", "--items", "cap")
    assert read_user(env) == {"autoCompactWindow": 200000}
    run(env, "undo", "--scope", "user")
    assert not user_settings_path(env).exists()


def test_undo_lifo_across_multiple_applies(env):
    original = {"env": {"A": "1"}}
    write_user(env, original)
    run(env, "apply", "--items", "cap")
    run(env, "apply", "--items", "env,read-cap")
    run(env, "apply", "--items", "concise", "--force-keys", "outputStyle")
    assert len(manifest(env)["entries"]) == 3
    run(env, "undo", "--scope", "user")
    assert read_user(env) == original


def test_symlinked_settings_survive(env, tmp_path):
    dot = tmp_path / "dotfiles"
    dot.mkdir()
    real = dot / "settings.json"
    real.write_text(json.dumps({"theme": "dark"}))
    link = user_settings_path(env)
    link.symlink_to(real)
    run(env, "apply", "--items", "cap")
    assert link.is_symlink()
    assert json.loads(real.read_text())["autoCompactWindow"] == 200000
    run(env, "undo", "--scope", "user")
    assert link.is_symlink() and json.loads(real.read_text()) == {"theme": "dark"}


def test_rereads_settings_right_before_writing(env, monkeypatch, capsys):
    """A concurrent edit (e.g. `rtk init`) between planning and writing must not be lost."""
    write_user(env, {"theme": "dark"})
    real_write_file = hs.write_file

    def write_file_and_simulate_concurrent_edit(plan, ts):
        data = json.loads(user_settings_path(env).read_text())
        data["hooks"] = {"PreToolUse": [{"hooks": [{"type": "command", "command": "rtk hook claude"}]}]}
        user_settings_path(env).write_text(json.dumps(data))
        return real_write_file(plan, ts)

    monkeypatch.setattr(hs, "write_file", write_file_and_simulate_concurrent_edit)
    assert hs.main(["--home", str(env["home"]), "--shell", "fish", "apply", "--items", "cap,explore"]) == 0
    s = read_user(env)
    assert s["autoCompactWindow"] == 200000 and "hooks" in s and s["theme"] == "dark"


# ---------------------------------------------------------------- statusline items

def test_statusline_item_installs_runnable_command(env):
    run(env, "apply", "--items", "statusline")
    installed = env["home"] / ".claude" / "hetz" / "statusline.py"
    assert installed.read_bytes() == open(os.path.join(SCRIPTS, "statusline.py"), "rb").read()
    assert installed.stat().st_mode & stat.S_IXUSR
    py = (env["home"] / ".claude" / "hetz" / "py").read_text().strip()
    assert os.path.isabs(py) and os.access(py, os.X_OK)
    cmd = read_user(env)["statusLine"]["command"]
    assert read_user(env)["statusLine"]["type"] == "command" and "hetz/statusline.py" in cmd
    e = dict(env["env"], NO_COLOR="1")
    out = subprocess.run(["sh", "-c", cmd], input=json.dumps(SAMPLE_STATUS_JSON), capture_output=True, text=True, env=e).stdout
    assert out.strip() == "sonnet·med │ ctx 142k/1M │ cache 96% warm │ $3.12"
    if shutil.which("fish"):
        assert subprocess.run(["fish", "--no-execute", "-c", cmd]).returncode == 0
    for sh in ("bash", "zsh"):
        if shutil.which(sh):
            assert subprocess.run([sh, "-n", "-c", cmd]).returncode == 0


def test_statusline_wrap_keeps_user_line_and_undo_restores(env):
    mine = {"type": "command", "command": "echo my-line", "padding": 1, "refreshInterval": 5}
    write_user(env, {"statusLine": mine})
    out = run(env, "apply", "--items", "statusline", "--dry-run").stdout
    assert "! statusLine" in out  # plain item never replaces a foreign statusline
    run(env, "apply", "--items", "statusline-wrap")
    s = read_user(env)
    assert "--wrap" in s["statusLine"]["command"]
    assert s["statusLine"]["padding"] == 1 and s["statusLine"]["refreshInterval"] == 5  # other keys survive the wrap
    wrap = json.loads((env["home"] / ".claude" / "hetz" / "statusline-wrap.json").read_text())
    assert wrap["command"] == "echo my-line"
    e = dict(env["env"], NO_COLOR="1")
    line = subprocess.run(["sh", "-c", s["statusLine"]["command"]], input=json.dumps(SAMPLE_STATUS_JSON),
                          capture_output=True, text=True, env=e).stdout.strip()
    assert line.startswith("my-line │ sonnet")
    st = run_json(env, "status")
    assert st["items"]["statusline-wrap"]["state"] == "on" and "statusline" not in st["items"]
    run(env, "undo", "--scope", "user")
    assert read_user(env) == {"statusLine": mine}
    assert not (env["home"] / ".claude" / "hetz" / "statusline-wrap.json").exists()


# ---------------------------------------------------------------- Explore override

def test_explore_template_frontmatter():
    text = open(EXPLORE_TEMPLATE).read()
    head, body = text.split("---\n", 2)[1:]
    fm = dict(line.split(": ", 1) for line in head.strip().splitlines())
    assert fm["name"] == "Explore" and fm["model"] == "haiku" and fm["effort"] == "low"
    assert fm["maxTurns"] == "25" and fm["omitClaudeMd"] == "true"
    assert fm["tools"] == "Read, Grep, Glob, Bash" and "Edit" in fm["disallowedTools"]
    assert fm["description"].startswith('"') and fm["description"].endswith('"')  # contains ": "
    assert "Answer:" in body and "Evidence:" in body and len(body.splitlines()) <= 20


def test_explore_conflict_force_and_restore(env):
    agents = env["home"] / ".claude" / "agents"
    agents.mkdir()
    mine = agents / "Explore.md"
    mine.write_text("---\nname: Explore\ndescription: mine\n---\nmy prompt\n")
    out = run(env, "apply", "--items", "explore").stdout
    assert "--force-keys file:Explore.md" in out
    assert mine.read_text().endswith("my prompt\n")
    assert run_json(env, "status")["items"]["explore"]["state"] == "conflict"
    run(env, "apply", "--items", "explore", "--force-keys", "file:Explore.md")
    assert mine.read_text() == open(EXPLORE_TEMPLATE).read()
    run(env, "undo", "--scope", "user")
    assert mine.read_text().endswith("my prompt\n")


def test_user_edit_of_installed_file_survives_undo(env):
    run(env, "apply", "--items", "explore")
    f = env["home"] / ".claude" / "agents" / "Explore.md"
    f.write_text(f.read_text() + "\nmy tweak\n")
    out = run(env, "undo", "--scope", "user").stdout
    assert "modified since apply; kept" in out
    assert f.read_text().endswith("my tweak\n")


def test_symlinked_explore_survives_force_and_undo(env, tmp_path):
    dot = tmp_path / "dotfiles"
    dot.mkdir()
    real = dot / "Explore.md"
    real.write_text("---\nname: Explore\ndescription: mine\n---\nmy prompt\n")
    agents = env["home"] / ".claude" / "agents"
    agents.mkdir()
    link = agents / "Explore.md"
    link.symlink_to(real)
    run(env, "apply", "--items", "explore", "--force-keys", "file:Explore.md")
    assert link.is_symlink() and real.read_text() == open(EXPLORE_TEMPLATE).read()
    run(env, "undo", "--scope", "user")
    assert link.is_symlink() and real.read_text().endswith("my prompt\n")
    assert sorted(os.listdir(agents)) == ["Explore.md"] and sorted(os.listdir(dot)) == ["Explore.md"]


# ---------------------------------------------------------------- launchers

def test_fish_launchers_and_collisions(env):
    fake_cc = env["bin"] / "cc"
    fake_cc.write_text("#!/bin/sh\n")
    fake_cc.chmod(0o755)
    fdir = env["home"] / ".config" / "fish" / "functions"
    fdir.mkdir(parents=True)
    (fdir / "cc-do.fish").write_text("function cc-do\nend\n")
    out = run(env, "apply", "--items", "launchers").stdout
    assert "cc shadows" in out
    assert "file:cc-do.fish" in out and (fdir / "cc-do.fish").read_text() == "function cc-do\nend\n"
    for name in ("cc", "cc-deep", "cc-quick", "cc-spec"):
        text = (fdir / (name + ".fish")).read_text()
        assert "function %s" % name in text
        if shutil.which("fish"):
            assert subprocess.run(["fish", "--no-execute", str(fdir / (name + ".fish"))]).returncode == 0
    assert '"/hetz:interview $argv"' in (fdir / "cc-spec.fish").read_text()
    run(env, "undo", "--scope", "user")
    assert sorted(os.listdir(fdir)) == ["cc-do.fish"]


def test_zsh_launchers_go_to_hetz_dir_without_rc_edits(env):
    e = dict(env["env"])
    proc = subprocess.run([sys.executable, "-I", SCRIPT, "--shell", "zsh", "apply", "--items", "launchers"],
                          env=e, capture_output=True, text=True, cwd=str(env["proj"]))
    assert proc.returncode == 0, proc.stderr
    f = env["home"] / ".claude" / "hetz" / "launchers.zsh"
    assert f.exists() and "cc-deep()" in f.read_text()
    assert "source ~/.claude/hetz/launchers.zsh" in proc.stdout
    assert not (env["home"] / ".zshrc").exists()
    if shutil.which("zsh"):
        assert subprocess.run(["zsh", "-n", str(f)]).returncode == 0


# ---------------------------------------------------------------- project / local scope

def test_local_scope_deny_and_git_exclude(env):
    proj = env["proj"]
    out = run(env, "apply", "--scope", "local", "--deny", "Read(./dist/**)", "--deny", "Read(**/*.min.js)").stdout
    assert "Applied." in out
    local = json.loads((proj / ".claude" / "settings.local.json").read_text())
    assert local == {"permissions": {"deny": ["Read(./dist/**)", "Read(**/*.min.js)"]}}
    exclude = proj / ".git" / "info" / "exclude"
    assert ".claude/settings.local.json" in exclude.read_text().splitlines()
    assert subprocess.run(["git", "-C", str(proj), "check-ignore", "-q", ".claude/settings.local.json"]).returncode == 0
    assert not (env["home"] / ".claude" / "settings.json").exists()
    run(env, "undo", "--scope", "local")
    assert not (proj / ".claude").exists()
    assert ".claude/settings.local.json" not in exclude.read_text().splitlines()


def test_project_scope_rejects_file_items_and_bad_rules(env):
    p = run(env, "apply", "--scope", "project", "--items", "explore", ok=False)
    assert p.returncode == 1 and "--scope user" in p.stderr
    p = run(env, "apply", "--scope", "project", "--deny", "rm -rf /", ok=False)
    assert p.returncode == 1 and "not a permission rule" in p.stderr
    run(env, "apply", "--scope", "project", "--deny", "Read(./build/**)", "--items", "cap")
    data = json.loads((env["proj"] / ".claude" / "settings.json").read_text())
    assert data == {"autoCompactWindow": 200000, "permissions": {"deny": ["Read(./build/**)"]}}
    assert ".claude/settings" not in (env["proj"] / ".git" / "info" / "exclude").read_text()


def test_repo_scope_backups_stay_out_of_the_work_tree(env):
    """A backup of settings.local.json (often holding tokens) must never become an untracked file in the repo."""
    proj = env["proj"]
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({"env": {"MY_TOKEN": "secret"}}))
    (proj / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))

    def untracked():
        return subprocess.run(["git", "-C", str(proj), "status", "--porcelain", "-uall"], capture_output=True,
                              text=True).stdout.split()

    before = untracked()
    run(env, "apply", "--scope", "local", "--deny", "Read(./build/**)")
    run(env, "apply", "--scope", "project", "--deny", "Read(./dist/**)")
    assert not any(".bak" in f for f in untracked())
    backups = list((env["home"] / ".claude" / "hetz" / "backups").rglob("*.hetz-*.bak"))
    assert sorted(b.name.split(".hetz-")[0] for b in backups) == ["settings.json", "settings.local.json"]
    m = manifest(env)
    assert all(e["backup"].startswith(str(env["home"])) for e in m["entries"])
    run(env, "undo", "--scope", "all")
    assert untracked() == before
    assert not [p for p in proj.rglob("*") if ".hetz-" in p.name]
    assert json.loads((proj / ".claude" / "settings.local.json").read_text()) == {"env": {"MY_TOKEN": "secret"}}


def test_undo_all_covers_every_scope(env):
    write_user(env, {"theme": "dark"})
    run(env, "apply", "--items", "cap,explore")
    run(env, "apply", "--scope", "local", "--deny", "Read(./dist/**)")
    out = run(env, "undo", "--scope", "all").stdout
    assert "Undone." in out
    assert read_user(env) == {"theme": "dark"}
    assert not (env["proj"] / ".claude" / "settings.local.json").exists()
    assert manifest(env) is None


# ---------------------------------------------------------------- deny-scan

def test_deny_scan_candidates(env):
    proj = env["proj"]
    (proj / ".gitignore").write_text("ignored/\n")
    for rel, size in {"dist/app.js": 300000, "ignored/big.min.js": 50000, "src/lib.min.js": 9000, "src/app.js.map": 9000,
                      "package-lock.json": 90000, "vendor/x/y.go": 20000, "data/fixture.json": 300000, "src/main.py": 300000,
                      "web/build/tiny.txt": 10}.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"a" * size)
    res = run_json(env, "deny-scan")
    rules = {c["rule"]: c for c in res["candidates"]}
    assert rules["Read(/dist/**)"]["recommended"] and rules["Read(/dist/**)"]["kind"] == "generated"
    assert "Read(**/*.min.js)" in rules and rules["Read(**/*.min.js)"]["files"] == 1  # ignored/ excluded
    assert "Read(**/*.js.map)" in rules
    assert not rules["Read(/package-lock.json)"]["recommended"]
    assert not rules["Read(/vendor/**)"]["recommended"]
    assert rules["Read(/data/fixture.json)"]["kind"] == "large data"
    assert not any("main.py" in r for r in rules)  # never source
    assert "Read(/web/build/**)" not in rules  # too small to be worth a rule
    run(env, "apply", "--scope", "local", "--deny", "Read(/dist/**)")
    rules = {c["rule"] for c in run_json(env, "deny-scan")["candidates"]}
    assert "Read(/dist/**)" not in rules
    text = run(env, "deny-scan").stdout
    assert "[x] Read(**/*.min.js)" in text and "[ ] Read(/package-lock.json)" in text


def test_deny_scan_build_named_dirs_need_generated_evidence(env):
    proj = env["proj"]
    files = {
        "build/webpack.1.js": "module.exports = {\n  mode: 'production',\n};\n" * 60,  # hand-written config
        "build/webpack.2.js": "const path = require('path');\n" * 120,
        "out/api_pb.go": "// Code generated by protoc-gen-go. DO NOT EDIT.\npackage api\n" + "var x = 1\n" * 800,
        "target/app/Main.class": "\0\x01binary" * 1000,
    }
    for rel, text in files.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    rules = {c["rule"]: c for c in run_json(env, "deny-scan")["candidates"]}
    assert not rules["Read(/build/**)"]["recommended"] and "build-named" in rules["Read(/build/**)"]["kind"]
    assert rules["Read(/out/**)"]["recommended"] and rules["Read(/out/**)"]["kind"] == "generated"
    assert rules["Read(/target/**)"]["recommended"]
    assert "[ ] Read(/build/**)" in run(env, "deny-scan").stdout


def test_partial_conflict_item_stays_recommended(env):
    write_user(env, {"env": {"MAX_MCP_OUTPUT_TOKENS": "30000"}, "outputStyle": "Explanatory"})
    st = run_json(env, "status")
    assert st["items"]["output-caps"]["state"] == "partial-conflict" and st["items"]["output-caps"]["recommended"]
    assert st["items"]["concise"]["state"] == "conflict" and not st["items"]["concise"]["recommended"]
    core = [i for g in st["ask"] if g["header"] in ("Core", "Setup") for i in g["items"]]
    assert "output-caps" in core
    assert "output-caps* — " in run(env, "status").stdout


# ---------------------------------------------------------------- pure functions

@pytest.mark.parametrize("current,desired", [
    ({}, {"a": 1, "b": {"c": [1, 2], "d": {"e": "x"}}}),
    ({"a": 1, "b": {"c": [2, 3]}}, {"a": 1, "b": {"c": [1, 2], "d": {"e": "x"}}}),
    ({"b": {"d": {"f": 1}}, "z": None}, {"b": {"d": {"e": "x"}}, "z": 0}),
    ({"env": {"X": "1"}, "permissions": {"deny": []}}, {"env": {"X": "2", "Y": "3"}, "permissions": {"deny": ["r"]}}),
])
def test_plan_apply_undo_roundtrip(current, desired):
    import copy as _copy
    ops = hs.plan_settings(current, desired, force={"z", "env.X"})
    changes = [op for op in ops if op["op"] in hs.CHANGE_OPS]
    doc = hs.apply_ops(_copy.deepcopy(current), changes)
    for key, val in desired.items():
        assert key in doc
    again = hs.plan_settings(doc, desired, force={"z", "env.X"})
    assert all(op["op"] == "same" for op in again)
    hs.undo_ops(doc, changes)
    assert doc == current


def test_conflict_is_not_applied_without_force():
    ops = hs.plan_settings({"env": {"X": "1"}, "statusLine": {"type": "command", "command": "a"}},
                           {"env": {"X": "2"}, "statusLine": {"type": "command", "command": "b"}})
    kinds = {".".join(op["path"]): op["op"] for op in ops}
    assert kinds == {"env.X": "conflict", "statusLine": "conflict"}  # statusLine is atomic, not merged


def test_chunk_sizes():
    for n in range(0, 17):
        groups = hs.chunk(list(range(n)))
        assert sum(len(g) for g in groups) == n
        assert all(len(g) <= 4 for g in groups)
        if n >= 2:
            assert all(len(g) >= 2 for g in groups)


def test_statusline_command_is_shell_neutral(tmp_path):
    paths = hs.Paths(str(tmp_path / "h"), str(tmp_path))
    cmd = hs.statusline_command(paths)
    assert cmd.startswith("sh -c '") and cmd.endswith("'") and cmd.count("'") == 2
    assert hs.is_hetz_statusline({"command": cmd}) and not hs.is_hetz_statusline({"command": "x"})
    weird = hs.Paths(str(tmp_path / "it's"), str(tmp_path))
    assert "'" not in hs.statusline_command(weird).replace(str(weird.hetz), "")


def test_profiles_are_consistent():
    items = hs.load_items()
    for iid, item in items.items():
        assert item["label"] and len(item["label"]) <= 60, iid
        assert item.get("settings") or item.get("files"), iid
        for kind in item.get("files", []):
            assert kind in hs.FILE_ITEMS, kind
    safe = json.load(open(os.path.join(PLUGIN, "profiles", "safe.json")))["items"]
    assert safe["cap"]["settings"] == {"autoCompactWindow": 200000}
    assert all(r.startswith("Read(//") for r in safe["glob-deny"]["settings"]["permissions"]["deny"])
    assert "Read(//**/package-lock.json)" not in safe["glob-deny"]["settings"]["permissions"]["deny"]
