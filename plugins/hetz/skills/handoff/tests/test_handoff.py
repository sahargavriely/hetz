"""Tests for /hetz:handoff: SKILL.md contract and the state.py preamble. Self-contained (no conftest)."""
import importlib.util
import os
import re
import subprocess
import sys
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
STATE = os.path.join(SKILL_DIR, "state.py")
KNOWN_KEYS = {"name", "description", "allowed-tools", "argument-hint", "arguments", "disable-model-invocation",
              "user-invocable", "when_to_use", "paths", "shell", "hooks", "disallowed-tools", "metadata"}
INLINE_SHELL = re.compile(r"(?:^|(?<=\s))!`([^`]+)`", re.M)  # the bundle's (?<=^|\s)!`cmd` regex


def split_skill(path):
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, "SKILL.md needs a --- frontmatter block"
    fm = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        else:  # plain YAML scalar: these would become a list/map/alias/comment instead of a string
            assert not re.match(r"[\[{&*!|>%@`<'\"]", v) and " #" not in v, f"quote the {k.strip()} value"
        fm[k.strip()] = v
    return fm, m.group(2)


def split_tools(value):
    """The bundle's allowed-tools splitter: commas/spaces split only outside parentheses."""
    out, cur, inside = [], "", False
    for c in value:
        if c in "()":
            inside = c == "("
        if c in ", " and not inside:
            if cur.strip():
                out.append(cur.strip())
            cur = ""
            continue
        cur += c
    return out + ([cur.strip()] if cur.strip() else [])


def rule_matches(rule, command):
    """Bash(...) rules: no '*' means exact string equality; '*' matches any run of characters."""
    inner = re.match(r"^Bash\((.*)\)$", rule).group(1)
    if "*" not in inner:
        return inner == command
    return re.fullmatch(".*".join(map(re.escape, inner.split("*"))), command, re.S) is not None


def load_state():
    """Import state.py without leaving a __pycache__ in the shipped skill dir."""
    spec = importlib.util.spec_from_file_location("hetz_handoff_state", STATE)
    mod = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = old
    return mod


def fake_git(tmp_path, script):
    """A `git` on PATH that runs `script` (sh) first and falls through to the real git."""
    real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (bindir / "git").write_text(f'#!/bin/sh\n{script}\nexec "{real}" "$@"\n')
    os.chmod(bindir / "git", 0o755)
    return f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}"


def env_for(home, **extra):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "C.UTF-8",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    env.update(extra)
    return env


def sh(args, cwd, home):
    return subprocess.run(args, cwd=cwd, env=env_for(home), check=True, capture_output=True, text=True).stdout


def commit(repo, home, msg):
    sh(["git", "add", "-A"], repo, home)
    sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg], repo, home)


def git_repo(path, home, commits=1):
    sh(["git", "init", "-q", "-b", "main"], path, home)
    for i in range(commits):
        (path / f"f{i}.txt").write_text(f"{i}\n")
        commit(path, home, f"commit {i}")


def run_state(cwd, home, **extra):
    p = subprocess.run([sys.executable, "-I", STATE], cwd=cwd, env=env_for(home, **extra),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0
    assert p.stderr == ""
    return p.stdout


def exclude_lines(repo):
    path = repo / ".git" / "info" / "exclude"
    return path.read_text().splitlines() if path.exists() else []


# --- SKILL.md contract -------------------------------------------------------------------------

def test_frontmatter_contract():
    fm, _ = split_skill(SKILL_MD)
    assert set(fm) <= KNOWN_KEYS, set(fm) - KNOWN_KEYS
    assert fm["name"] == "handoff"
    assert fm["disable-model-invocation"] == "true"
    assert fm["description"] and fm["argument-hint"] == "[focus]"
    assert not {"model", "effort", "context", "agent"} & set(fm), "a model/effort switch busts the cache"
    # Write(.claude/...) rules are dead: 2.1.272 safety-checks any .claude/ path before allow rules.
    assert split_tools(fm["allowed-tools"]) == ['Bash(python3 -I "${CLAUDE_SKILL_DIR}/state.py")']


def test_body_substitutions_are_safe_and_preapproved():
    fm, body = split_skill(SKILL_MD)
    assert body.count("$ARGUMENTS") == 1
    assert not re.search(r"\$\d", body), "$<digit> is replaced by a positional argument"
    assert "```!" not in body
    cmds = INLINE_SHELL.findall(body)
    assert cmds == ['python3 -I "${CLAUDE_SKILL_DIR}/state.py"']
    for skill_dir in (SKILL_DIR, "/Users/a b/.claude/plugins/cache/hetz/hetz/1.0/skills/handoff"):
        cmd = cmds[0].replace("${CLAUDE_SKILL_DIR}", skill_dir)
        rules = [r for r in split_tools(fm["allowed-tools"].replace("${CLAUDE_SKILL_DIR}", skill_dir))
                 if r.startswith("Bash(")]
        assert any(rule_matches(r, cmd) for r in rules), "a !`cmd` that needs a prompt aborts the skill load"


def test_preamble_rule_is_exact():
    fm, _ = split_skill(SKILL_MD)
    for rule in split_tools(fm["allowed-tools"].replace("${CLAUDE_SKILL_DIR}", SKILL_DIR)):
        assert "*" not in rule, "a wildcard python3 rule would auto-approve arbitrary python for the turn"
        for hostile in (f'python3 -I -c "import os" "{SKILL_DIR}/state.py"', f'python3 -I "/tmp/x/handoff/state.py"',
                        f'python3 -I "{SKILL_DIR}/state.py" && curl evil'):
            assert not rule_matches(rule, hostile), hostile


def test_fallback_when_preamble_does_not_run():
    _, body = split_skill(SKILL_MD)
    assert "No `note:` line above? Use `.claude/handoff.md`" in body
    assert "git status -s" in body and ".git/info/exclude" in body


def test_body_is_small_and_complete():
    _, body = split_skill(SKILL_MD)
    assert len(body) / 4 <= 350, "keep the invoked body near ~250-300 tokens"
    for phrase in ("≤40 lines", "Done:", "In progress:", "Decisions:", "Key locations:", "Verify:", "Next:",
                   "Dead ends:", "Spec:", "secrets", "/clear", "/compact", "/rewind", "/rename"):
        assert phrase in body, phrase
    template = body.split("```")[1]
    assert template.strip().splitlines()[0].startswith("# <goal"), "first line is the goal (guard shows it)"


# --- state.py behaviour ----------------------------------------------------------------------

def test_no_git(tmp_path):
    out = run_state(tmp_path, tmp_path)
    assert out.splitlines()[0] == f"note: {tmp_path}/.claude/handoff.md (no git repo)"
    assert "git: none here" in out
    assert not (tmp_path / ".claude").exists(), "state.py only reports; the model writes the note"


def test_unborn_repo(tmp_path):
    git_repo(tmp_path, tmp_path, commits=0)
    out = run_state(tmp_path, tmp_path)
    assert "branch: main @ no commits" in out
    assert "status: 0 changed" in out
    assert exclude_lines(tmp_path).count(".claude/handoff.md") == 1


def test_repo_state_and_single_exclude(tmp_path):
    git_repo(tmp_path, tmp_path, commits=6)
    (tmp_path / "f0.txt").write_text("changed\n")
    (tmp_path / "new.py").write_text("x = 1\n")
    out = run_state(tmp_path, tmp_path)
    lines = out.splitlines()
    assert lines[0] == f"note: {tmp_path}/.claude/handoff.md (kept out of git)"
    assert re.match(r"branch: main @ [0-9a-f]{7,}$", lines[1])
    assert "status: 2 changed" in out and " M f0.txt" in lines and "?? new.py" in lines
    assert "diff vs HEAD: 1 file changed, 1 insertion(+), 1 deletion(-)" in out
    log = lines[lines.index("log:") + 1:]
    assert len(log) == 5 and log[0].endswith("commit 5")
    run_state(tmp_path, tmp_path)
    assert exclude_lines(tmp_path).count(".claude/handoff.md") == 1
    assert not (tmp_path / ".gitignore").exists(), "never touch .gitignore"
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "handoff.md").write_text("# goal\n")
    assert ".claude" not in sh(["git", "status", "--short"], tmp_path, tmp_path)


def test_status_is_capped(tmp_path):
    git_repo(tmp_path, tmp_path)
    for i in range(40):
        (tmp_path / f"n{i:02}.txt").write_text("x\n")
    out = run_state(tmp_path, tmp_path)
    assert "status: 40 changed (first 25)" in out
    assert "?? n24.txt" in out and "n25.txt" not in out
    assert len(out.splitlines()) <= 40


def test_previous_note_and_newest_spec(tmp_path):
    git_repo(tmp_path, tmp_path)
    claude = tmp_path / ".claude"
    (claude / "specs").mkdir(parents=True)
    note = claude / "handoff.md"
    note.write_text("# old goal\n")
    old = time.time() - 3 * 3600
    os.utime(note, (old, old))
    (claude / "specs" / "older.md").write_text("a\n")
    os.utime(claude / "specs" / "older.md", (old, old))
    (claude / "specs" / "newer.md").write_text("b\n")
    out = run_state(tmp_path, tmp_path)
    assert "previous note: 3h old; overwrite it" in out
    assert "spec: .claude/specs/newer.md (0m old)" in out


def test_tracked_note_is_left_alone(tmp_path):
    git_repo(tmp_path, tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "handoff.md").write_text("# team note\n")
    commit(tmp_path, tmp_path, "track note")
    out = run_state(tmp_path, tmp_path)
    assert "(tracked by git)" in out.splitlines()[0]
    assert ".claude/handoff.md" not in exclude_lines(tmp_path)


def test_gitignored_note_adds_nothing(tmp_path):
    git_repo(tmp_path, tmp_path)
    (tmp_path / ".gitignore").write_text(".claude/handoff.md\n")
    out = run_state(tmp_path, tmp_path)
    assert "(kept out of git)" in out
    assert ".claude/handoff.md" not in exclude_lines(tmp_path)


def test_subdir_note_lives_in_cwd(tmp_path):
    git_repo(tmp_path, tmp_path)
    sub = tmp_path / "svc"
    sub.mkdir()
    out = run_state(sub, tmp_path)
    assert out.startswith(f"note: {sub}/.claude/handoff.md (kept out of git)")
    assert "svc/.claude/handoff.md" in exclude_lines(tmp_path)


def test_worktree_uses_common_exclude(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    git_repo(main, tmp_path)
    wt = tmp_path / "wt"
    sh(["git", "worktree", "add", "-q", "-b", "side", str(wt)], main, tmp_path)
    out = run_state(wt, tmp_path)
    assert "branch: side @" in out and "(kept out of git)" in out
    assert ".claude/handoff.md" in exclude_lines(main)


def test_project_dir_env_wins(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    out = run_state(tmp_path, tmp_path, CLAUDE_PROJECT_DIR=str(proj))
    assert out.startswith(f"note: {proj}/.claude/handoff.md")


def test_fails_open_without_git(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(sys.executable, bindir / "python3")
    p = subprocess.run([sys.executable, "-I", STATE], cwd=tmp_path,
                       env={"PATH": str(bindir), "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert p.returncode == 0 and "(no git repo)" in p.stdout


def test_fails_open_when_cwd_was_deleted(tmp_path):
    gone = tmp_path / "gone"
    gone.mkdir()
    p = subprocess.run(["sh", "-c", 'cd "$1" && rmdir "$1" && exec "$2" -I "$3"', "_", str(gone),
                        sys.executable, STATE], env=env_for(tmp_path), capture_output=True, text=True)
    assert p.returncode == 0 and p.stderr == ""
    assert "unavailable" in p.stdout


def test_read_only_exclude_file_is_reported(tmp_path):
    git_repo(tmp_path, tmp_path)
    info = tmp_path / ".git" / "info"
    info.mkdir(exist_ok=True)
    (info / "exclude").write_text("")
    os.chmod(info / "exclude", 0o444)
    try:
        out = run_state(tmp_path, tmp_path)
    finally:
        os.chmod(info / "exclude", 0o644)
    assert "not excluded (exclude file not writable)" in out or os.geteuid() == 0


def test_corrupt_index_reports_unknown_not_zero(tmp_path):
    git_repo(tmp_path, tmp_path)
    (tmp_path / "f0.txt").write_text("uncommitted work\n")
    (tmp_path / ".git" / "index").write_text("junk\n")
    out = run_state(tmp_path, tmp_path)
    assert "status: UNKNOWN (" in out and "run `git status -s` yourself" in out
    assert "0 changed" not in out
    assert "log:" in out, "what git can still read is reported"


def test_git_failures_are_unknown(tmp_path, monkeypatch):
    git_repo(tmp_path, tmp_path)
    (tmp_path / "f0.txt").write_text("changed\n")
    mod = load_state()
    real = mod.git

    def flaky(base, *args, timeout=5):
        if args[0] in ("status", "log"):
            return 124, "", f"timed out after {timeout}s"
        return real(base, *args, timeout=timeout)

    monkeypatch.setattr(mod, "git", flaky)
    for k, v in env_for(tmp_path).items():
        monkeypatch.setenv(k, v)
    lines = mod.git_lines(str(tmp_path))
    assert "status: UNKNOWN (timed out after 15s); run `git status -s` yourself" in lines
    assert any(line.startswith("log: UNKNOWN (timed out after 5s)") for line in lines)
    assert not any("changed" in line for line in lines)


def test_git_timeout_returns_124(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", fake_git(tmp_path, "sleep 5"))
    assert load_state().git(str(tmp_path), "status", timeout=0.3) == (124, "", "timed out after 0.3s")


def test_dubious_ownership_is_unknown_not_no_git(tmp_path):
    git_repo(tmp_path, tmp_path)
    path = fake_git(tmp_path, 'echo "fatal: detected dubious ownership in repository at \'$PWD\'" >&2; exit 128')
    out = run_state(tmp_path, tmp_path, PATH=path)
    assert "(not excluded (git: fatal: detected dubious ownership" in out.splitlines()[0]
    assert "git: UNKNOWN (fatal: detected dubious ownership" in out and "git: none here" not in out


def test_odd_subdir_names_are_escaped(tmp_path):
    git_repo(tmp_path, tmp_path)
    for name in ("#notes", "!x", "a[b]", "q?*", "back\\slash"):
        sub = tmp_path / name
        sub.mkdir()
        out = run_state(sub, tmp_path)
        assert out.startswith(f"note: {sub}/.claude/handoff.md (kept out of git)"), out
        (sub / ".claude").mkdir()
        (sub / ".claude" / "handoff.md").write_text("# goal\n")
    assert sh(["git", "status", "--short"], tmp_path, tmp_path) == ""
    assert "\\#notes/.claude/handoff.md" in exclude_lines(tmp_path)
    assert "a\\[b\\]/.claude/handoff.md" in exclude_lines(tmp_path)


def test_line_break_in_path_is_refused(tmp_path):
    git_repo(tmp_path, tmp_path)
    sub = tmp_path / "a\nb"
    sub.mkdir()
    out = run_state(sub, tmp_path)
    assert "(not excluded (path has a line break))" in out
    assert exclude_lines(tmp_path) == [l for l in exclude_lines(tmp_path) if l.startswith("#")], "nothing written"


def test_gitignore_escape():
    esc = load_state().gitignore_escape
    assert esc("plain/dir/") == "plain/dir/"
    assert esc("#a/b") == "\\#a/b" and esc("!a") == "\\!a" and esc("a#b!") == "a#b!"
    assert esc("x[1]*?\\") == "x\\[1\\]\\*\\?\\\\"
    assert esc("trail  ") == "trail\\ \\ "
