"""Tests for /hetz:interview: SKILL.md contract and the prep.py preamble. Self-contained (no conftest)."""
import ast
import os
import re
import shutil
import subprocess
import sys

import pytest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
PREP = os.path.join(SKILL_DIR, "prep.py")
BANK = os.path.join(SKILL_DIR, "references", "question-bank.md")
HANDOFF_STATE = os.path.join(os.path.dirname(SKILL_DIR), "handoff", "state.py")
SHARED = ("git", "repo_state", "gitignore_escape", "ensure_excluded")
# Frontmatter keys Claude Code 2.1.272 reads for skills (from the bundle's frontmatter schema).
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


def shared_sources(path):
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines(keepends=True)
    return {n.name: "".join(lines[n.lineno - 1:n.end_lineno]) for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef) and n.name in SHARED}


def env_for(home, **extra):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "C.UTF-8",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    env.update(extra)
    return env


def sh(args, cwd, home):
    subprocess.run(args, cwd=cwd, env=env_for(home), check=True, capture_output=True)


def git_repo(path, home, commit=True):
    sh(["git", "init", "-q", "-b", "main"], path, home)
    if commit:
        (path / "README.md").write_text("hi\n")
        sh(["git", "add", "-A"], path, home)
        sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], path, home)


def run_prep(cwd, home, **extra):
    p = subprocess.run([sys.executable, "-I", PREP], cwd=cwd, env=env_for(home, **extra),
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
    assert fm["name"] == "interview"
    assert fm["disable-model-invocation"] == "true"  # 0 listing tokens
    assert fm["description"] and fm["argument-hint"]
    assert not {"model", "effort", "context", "agent"} & set(fm), "a model/effort switch busts the cache"
    tools = split_tools(fm["allowed-tools"])
    for want in ("Agent(Explore)", "Read", "Grep", "Glob", 'Bash(python3 -I "${CLAUDE_SKILL_DIR}/prep.py")'):
        assert want in tools
    # Write(.claude/...) rules are dead: 2.1.272 safety-checks any .claude/ path before allow rules.
    assert not [t for t in tools if t.startswith("Write(.claude")]


def test_body_substitutions_are_safe():
    _, body = split_skill(SKILL_MD)
    assert body.count("$ARGUMENTS") == 1
    assert not re.search(r"\$\d", body), "$<digit> is replaced by a positional argument"
    assert "```!" not in body
    cmds = INLINE_SHELL.findall(body)
    assert cmds == ['python3 -I "${CLAUDE_SKILL_DIR}/prep.py"']
    assert "$ARGUMENTS" not in cmds[0], "user text must never reach a shell block"


def test_preamble_is_preapproved():
    fm, body = split_skill(SKILL_MD)
    for skill_dir in (SKILL_DIR, "/Users/a b/.claude/plugins/cache/hetz/hetz/1.0/skills/interview"):
        cmd = INLINE_SHELL.findall(body)[0].replace("${CLAUDE_SKILL_DIR}", skill_dir)
        rules = [r for r in split_tools(fm["allowed-tools"].replace("${CLAUDE_SKILL_DIR}", skill_dir))
                 if r.startswith("Bash(")]
        assert any(rule_matches(r, cmd) for r in rules), "a !`cmd` that needs a prompt aborts the skill load"


def test_python_rule_is_exact():
    fm, _ = split_skill(SKILL_MD)
    rules = [r for r in split_tools(fm["allowed-tools"].replace("${CLAUDE_SKILL_DIR}", SKILL_DIR))
             if r.startswith("Bash(python")]
    assert len(rules) == 1 and "*" not in rules[0], "a wildcard python3 rule auto-approves arbitrary python"
    for hostile in (f'python3 -I -c "import os" "{SKILL_DIR}/prep.py"', 'python3 -I "/tmp/x/interview/prep.py"',
                    f'python3 -I "{SKILL_DIR}/prep.py" extra'):
        assert not rule_matches(rules[0], hostile), hostile


def test_fallback_when_preamble_does_not_run():
    _, body = split_skill(SKILL_MD)
    assert "none shown? add `.claude/specs/` to .git/info/exclude" in body


def test_shared_helpers_match_handoff():
    mine, theirs = shared_sources(PREP), shared_sources(HANDOFF_STATE)
    assert set(mine) == set(SHARED)
    for name in SHARED:
        assert mine[name] == theirs.get(name), f"{name} drifted from handoff/state.py; keep the copies identical"


def test_body_is_small_and_references_exist():
    _, body = split_skill(SKILL_MD)
    assert len(body) / 4 <= 500, "keep the invoked body near ~450 tokens; detail goes to references/"
    assert "${CLAUDE_SKILL_DIR}/references/question-bank.md" in body
    assert os.path.isfile(BANK)
    for phrase in ("AskUserQuestion", "Explore", "enough", "Recommended", "Spec: <path>. Fresh start: /clear",
                   "/hetz-crew:delegate", "Anything I haven't asked"):
        assert phrase in body or phrase.lower() in body.lower(), phrase


def test_question_bank_has_spec_format_and_plan_tags():
    bank = open(BANK, encoding="utf-8").read()
    assert "## Spec format" in bank and "## Rework ranking" in bank
    for section in ("Goal:", "Non-goals:", "## Decisions", "## Interfaces", "## Acceptance checks",
                    "## Files likely touched", "## Risks / unknowns", "## Plan"):
        assert section in bank, section
    for tag in ("inline", "Explore", "runner", "worker", "judge"):
        assert f"- {tag}:" in bank, tag
    assert "(user|default)" in bank
    assert len(bank) / 4 < 2000, "reference stays cheap to read"


# --- prep.py behaviour -----------------------------------------------------------------------

def test_no_git_dir(tmp_path):
    out = run_prep(tmp_path, tmp_path)
    assert f"dir: {tmp_path} (no git)" in out
    assert "(no git repo)" in out
    assert not (tmp_path / ".gitignore").exists()


def test_git_repo_excludes_specs_once(tmp_path):
    git_repo(tmp_path, tmp_path)
    out = run_prep(tmp_path, tmp_path)
    assert out.startswith(f"repo: {tmp_path} (branch main, 0 uncommitted)")
    assert "recent commits:" in out and "init" in out
    assert f"specs dir: {tmp_path}/.claude/specs/ (kept out of git)" in out
    run_prep(tmp_path, tmp_path)
    assert exclude_lines(tmp_path).count(".claude/specs/") == 1
    assert not (tmp_path / ".gitignore").exists(), "never touch .gitignore"
    (tmp_path / ".claude" / "specs").mkdir(parents=True)
    (tmp_path / ".claude" / "specs" / "x.md").write_text("spec\n")
    st = subprocess.run(["git", "status", "--short"], cwd=tmp_path, env=env_for(tmp_path),
                        capture_output=True, text=True).stdout
    assert ".claude" not in st


def test_exclude_appends_after_missing_newline(tmp_path):
    git_repo(tmp_path, tmp_path)
    (tmp_path / ".git" / "info").mkdir(exist_ok=True)
    (tmp_path / ".git" / "info" / "exclude").write_text("*.log")
    run_prep(tmp_path, tmp_path)
    assert exclude_lines(tmp_path) == ["*.log", ".claude/specs/"]


def test_already_ignored_or_tracked_is_left_alone(tmp_path):
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    git_repo(ignored, tmp_path)
    (ignored / ".gitignore").write_text(".claude/\n")
    out = run_prep(ignored, tmp_path)
    assert "(kept out of git)" in out
    assert ".claude/specs/" not in exclude_lines(ignored)

    tracked = tmp_path / "tracked"
    (tracked / ".claude" / "specs").mkdir(parents=True)
    (tracked / ".claude" / "specs" / "team.md").write_text("x\n")
    git_repo(tracked, tmp_path)
    out = run_prep(tracked, tmp_path)
    assert "(tracked by git); existing: team.md" in out
    assert ".claude/specs/" not in exclude_lines(tracked)


def test_subdir_pattern_is_anchored_to_the_subdir(tmp_path):
    git_repo(tmp_path, tmp_path)
    sub = tmp_path / "pkg" / "api"
    sub.mkdir(parents=True)
    out = run_prep(sub, tmp_path)
    assert "cwd pkg/api" in out
    assert f"specs dir: {sub}/.claude/specs/" in out
    assert "pkg/api/.claude/specs/" in exclude_lines(tmp_path)


def test_build_hints_and_existing_specs(tmp_path):
    git_repo(tmp_path, tmp_path, commit=False)
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest", "build": "tsc"}}')
    (tmp_path / "Makefile").write_text(".PHONY: all\nall: build\nbuild:\n\techo\nVAR := 1\ntest: build\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n[tool.pytest.ini_options]\n[tool.ruff]\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    (tmp_path / "src").mkdir()
    (tmp_path / ".claude" / "specs").mkdir(parents=True)
    (tmp_path / ".claude" / "specs" / "old-task.md").write_text("x\n")
    out = run_prep(tmp_path, tmp_path)
    assert "top level: Makefile, package.json, pyproject.toml, src/" in out
    assert "manifests: pyproject.toml, package.json, Makefile" in out
    assert "npm scripts: test, build" in out
    assert "Makefile: all, build, test" in out
    assert "pyproject tools: pytest, ruff" in out
    assert "CI: ci.yml" in out
    assert "existing: old-task.md" in out
    assert len(out.splitlines()) <= 15


def test_broken_manifests_do_not_crash(tmp_path):
    (tmp_path / "package.json").write_text("{not json")
    (tmp_path / "Makefile").write_bytes(b"\xff\xfe\x00garbage")
    out = run_prep(tmp_path, tmp_path)
    assert "manifests: package.json, Makefile" in out


def test_project_dir_env_wins(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    out = run_prep(tmp_path, tmp_path, CLAUDE_PROJECT_DIR=str(proj))
    assert f"dir: {proj} (no git)" in out


def test_fails_open_without_git(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(sys.executable, bindir / "python3")
    p = subprocess.run([sys.executable, "-I", PREP], cwd=tmp_path,
                       env={"PATH": str(bindir), "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert p.returncode == 0 and "(no git)" in p.stdout


def test_fails_open_when_cwd_was_deleted(tmp_path):
    gone = tmp_path / "gone"
    gone.mkdir()
    p = subprocess.run(["sh", "-c", 'cd "$1" && rmdir "$1" && exec "$2" -I "$3"', "_", str(gone),
                        sys.executable, PREP], env=env_for(tmp_path), capture_output=True, text=True)
    assert p.returncode == 0 and p.stderr == ""
    assert "unavailable" in p.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_output_never_contains_file_contents(tmp_path):
    git_repo(tmp_path, tmp_path)
    (tmp_path / "secret.env").write_text("API_KEY=sk-live-123\n")
    out = run_prep(tmp_path, tmp_path)
    assert "sk-live-123" not in out


def test_corrupt_index_reports_unknown_not_zero(tmp_path):
    git_repo(tmp_path, tmp_path)
    (tmp_path / "README.md").write_text("uncommitted\n")
    (tmp_path / ".git" / "index").write_text("junk\n")
    out = run_prep(tmp_path, tmp_path)
    assert "uncommitted UNKNOWN" in out and "0 uncommitted" not in out


def test_odd_subdir_names_are_escaped(tmp_path):
    git_repo(tmp_path, tmp_path)
    for name in ("#notes", "!x", "app[slug]"):
        sub = tmp_path / name
        sub.mkdir()
        out = run_prep(sub, tmp_path)
        assert f"specs dir: {sub}/.claude/specs/ (kept out of git)" in out, out
        (sub / ".claude" / "specs").mkdir(parents=True)
        (sub / ".claude" / "specs" / "s.md").write_text("x\n")
    st = subprocess.run(["git", "status", "--short"], cwd=tmp_path, env=env_for(tmp_path),
                        capture_output=True, text=True).stdout
    assert st == ""
    assert "app\\[slug\\]/.claude/specs/" in exclude_lines(tmp_path)
