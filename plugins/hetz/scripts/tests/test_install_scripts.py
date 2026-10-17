"""Tests for scripts/install.sh and scripts/uninstall.sh against a stub `claude` (PATH has no real claude),
with HETZ_HOME pointing at a temp home. Nothing here can reach the real Claude Code install."""
import json
import os
import shutil
import subprocess
import sys

import pytest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPTS)))
INSTALL = os.path.join(REPO, "scripts", "install.sh")
UNINSTALL = os.path.join(REPO, "scripts", "uninstall.sh")
SETTINGS = os.path.join(SCRIPTS, "hetz_settings.py")

STUB = r'''#!/bin/sh
# stub claude: logs argv, keeps marketplace/plugin state in $STUB_STATE
echo "$*" >> "$STUB_LOG"
state="$STUB_STATE"
case "$*" in
  "--version") echo "${STUB_VERSION:-2.1.272} (Claude Code)" ;;
  "plugin marketplace list --json")
    if grep -q '^market$' "$state" 2>/dev/null; then echo '[{"name": "hetz", "source": "directory"}]'; else echo '[]'; fi ;;
  "plugin list --json")
    printf '['; sep=''
    for p in hetz hetz-crew; do
      if grep -q "^plugin $p\$" "$state" 2>/dev/null; then printf '%s{"id": "%s@hetz", "scope": "user"}' "$sep" "$p"; sep=','; fi
    done
    echo ']' ;;
  "plugin marketplace add "*) echo market >> "$state" ;;
  "plugin install "*) p=${3%@hetz}; echo "plugin $p" >> "$state" ;;
  "plugin uninstall "*) p=${3%@hetz}; grep -v "^plugin $p\$" "$state" > "$state.tmp"; mv "$state.tmp" "$state" ;;
  "plugin marketplace remove hetz") grep -v '^market$' "$state" > "$state.tmp"; mv "$state.tmp" "$state" ;;
  *) echo "stub: unexpected $*" >&2; exit 9 ;;
esac
'''


@pytest.fixture
def stub(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(STUB)
    claude.chmod(0o755)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    path = os.pathsep.join([str(bin_dir), os.path.dirname(os.path.realpath(sys.executable)), "/usr/bin", "/bin"])
    assert shutil.which("claude", path=path) == str(claude)  # never the real one
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC", "HETZ"))}
    env.update(PATH=path, HETZ_HOME=str(home), STUB_LOG=str(tmp_path / "log"), STUB_STATE=str(tmp_path / "state"))
    return {"env": env, "home": home, "log": tmp_path / "log", "state": tmp_path / "state", "tmp": tmp_path}


def sh(stub, script, *args, extra_env=None):
    env = dict(stub["env"])
    env.update(extra_env or {})
    return subprocess.run(["bash", script, *args], env=env, capture_output=True, text=True, timeout=60)


def calls(stub):
    return stub["log"].read_text().splitlines() if stub["log"].exists() else []


MUTATING = ("plugin marketplace add", "plugin install", "plugin uninstall", "plugin marketplace remove")


def test_scripts_parse():
    for script in (INSTALL, UNINSTALL):
        assert subprocess.run(["bash", "-n", script]).returncode == 0
        assert os.access(script, os.X_OK)


def test_unknown_option(stub):
    assert sh(stub, INSTALL, "--bogus").returncode == 2
    assert sh(stub, UNINSTALL, "--bogus").returncode == 2


def test_install_dry_run_prints_commands_only(stub):
    p = sh(stub, INSTALL, "--dry-run")
    assert p.returncode == 0, p.stderr
    out = p.stdout
    assert "+ claude plugin marketplace add %s" % REPO in out
    assert "+ claude plugin install hetz@hetz" in out and "+ claude plugin install hetz-crew@hetz" in out
    assert "/hetz:setup global" in out
    assert not any(c.startswith(MUTATING) for c in calls(stub))
    assert not (stub["home"] / ".claude" / "hetz" / "py").exists()


def test_install_is_idempotent(stub):
    p = sh(stub, INSTALL)
    assert p.returncode == 0, p.stderr
    made = [c for c in calls(stub) if c.startswith(MUTATING)]
    assert made == ["plugin marketplace add %s" % REPO, "plugin install hetz@hetz", "plugin install hetz-crew@hetz"]
    py = (stub["home"] / ".claude" / "hetz" / "py").read_text().strip()
    assert os.path.isabs(py) and os.access(py, os.X_OK) and "shims" not in py
    stub["log"].unlink()
    p = sh(stub, INSTALL)
    assert p.returncode == 0
    assert not any(c.startswith(MUTATING) for c in calls(stub))
    assert "= marketplace hetz already added" in p.stdout and "= hetz@hetz already installed" in p.stdout
    assert "already points at" in p.stdout


def test_install_warns_on_old_version(stub):
    p = sh(stub, INSTALL, "--dry-run", extra_env={"STUB_VERSION": "2.1.200"})
    assert p.returncode == 0 and "older than 2.1.272" in p.stdout


def test_install_without_claude_fails_unless_dry_run(stub, tmp_path):
    env_path = os.pathsep.join([os.path.dirname(os.path.realpath(sys.executable)), "/usr/bin", "/bin"])
    if shutil.which("claude", path=env_path):
        pytest.skip("a claude binary lives in a system dir")
    p = sh(stub, INSTALL, extra_env={"PATH": env_path})
    assert p.returncode == 1 and "not on PATH" in p.stderr


def test_uninstall_dry_run_previews_undo_and_commands(stub):
    sh(stub, INSTALL)
    env = dict(stub["env"])
    subprocess.run([sys.executable, "-I", SETTINGS, "--shell", "fish", "apply", "--items", "cap,explore"], env=env, check=True,
                   capture_output=True, cwd=str(stub["tmp"]))
    settings = stub["home"] / ".claude" / "settings.json"
    before = settings.read_text()
    stub["log"].unlink()
    p = sh(stub, UNINSTALL, "--dry-run")
    assert p.returncode == 0, p.stderr
    assert "hetz undo (dry run)" in p.stdout and "autoCompactWindow" in p.stdout
    assert "+ claude plugin uninstall hetz-crew@hetz" in p.stdout and "+ claude plugin uninstall hetz@hetz" in p.stdout
    assert "+ claude plugin marketplace remove hetz" in p.stdout
    assert not any(c.startswith(MUTATING) for c in calls(stub))
    assert settings.read_text() == before
    assert (stub["home"] / ".claude" / "agents" / "Explore.md").exists()


def test_uninstall_restores_and_is_idempotent(stub):
    settings = stub["home"] / ".claude" / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}))
    sh(stub, INSTALL)
    subprocess.run([sys.executable, "-I", SETTINGS, "--shell", "fish", "apply", "--items", "cap,statusline,launchers"],
                   env=stub["env"], check=True, capture_output=True, cwd=str(stub["tmp"]))
    p = sh(stub, UNINSTALL)
    assert p.returncode == 0, p.stderr
    assert json.loads(settings.read_text()) == {"theme": "dark"}
    assert not (stub["home"] / ".claude" / "hetz" / "py").exists()
    assert not (stub["home"] / ".config").exists()
    assert stub["state"].read_text().strip() == ""
    stub["log"].unlink()
    p = sh(stub, UNINSTALL)
    assert p.returncode == 0
    assert "= no hetz manifest" in p.stdout and "= hetz@hetz not installed" in p.stdout
    assert not any(c.startswith(MUTATING) for c in calls(stub))


def test_uninstall_keep_settings_and_purge(stub):
    sh(stub, INSTALL)
    subprocess.run([sys.executable, "-I", SETTINGS, "apply", "--items", "cap"], env=stub["env"], check=True,
                   capture_output=True, cwd=str(stub["tmp"]))
    p = sh(stub, UNINSTALL, "--keep-settings", "--purge")
    assert p.returncode == 0, p.stderr
    assert json.loads((stub["home"] / ".claude" / "settings.json").read_text()) == {"autoCompactWindow": 200000}
    assert not (stub["home"] / ".claude" / "hetz").exists()
