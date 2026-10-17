"""Tests for /hetz:slim: slim_audit.py, SKILL.md and the compact-instructions template.

Self-contained (no conftest). Every test uses temp dirs and a fake HOME; nothing touches the real ~/.claude.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SKILL_DIR, "slim_audit.py")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
TEMPLATE = os.path.join(SKILL_DIR, "..", "..", "templates", "compact-instructions.md")
CANARY = "CANARY_7f3e_DO_NOT_PRINT"
HAS_GIT = shutil.which("git") is not None
CLEAR_ENV = ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GLOB_NO_IGNORE", "ENABLE_TOOL_SEARCH", "ANTHROPIC_BASE_URL",
             "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_CODE_REMOTE_MEMORY_DIR", "CLAUDE_PROJECT_DIR")

spec = importlib.util.spec_from_file_location("slim_audit", SCRIPT)
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)


# ---------- helpers ----------

def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def env_for(home, extra=None):
    env = {k: v for k, v in os.environ.items() if k not in CLEAR_ENV}
    env.update(HOME=home, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    env.update(extra or {})
    return env


def audit(cwd, home, *args, extra_env=None):
    p = subprocess.run([sys.executable, "-I", SCRIPT, "--cwd", cwd, *args], capture_output=True, text=True,
                       env=env_for(home, extra_env), timeout=60)
    return p.returncode, p.stdout, p.stderr


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True,
                   env=env_for(os.environ.get("HOME", "/tmp")))


def mem_dir(home, root):
    return os.path.join(home, ".claude", "projects", re.sub(r"[^a-zA-Z0-9]", "-", os.path.realpath(root)), "memory")


def build_fixture(tmp, with_git=True):
    home, repo = os.path.join(tmp, "home"), os.path.join(tmp, "repo")
    w(os.path.join(home, ".claude", "CLAUDE.md"), "".join("- user rule %d %s\n" % (i, CANARY) for i in range(70)))
    w(os.path.join(home, ".claude", "settings.json"),
      json.dumps({"permissions": {"deny": ["Read(//**/*.min.js)", "Read(//**/*.min.css)", "Bash(rm *)"]}}))
    w(os.path.join(home, ".claude.json"), json.dumps({"mcpServers": {"github": {"command": "x", "env": {"TOKEN": CANARY}}}}))
    claude_md = ("# Project\n<!-- maintainer note %s\nsecond line -->\n"
                 "See @docs/arch.md and `@code.md` and mail me@example.com\n"
                 "```\n@fenced.md\n```\n" % CANARY) + "".join("- line %d\n" % i for i in range(250)) + "@missing.md\n"
    w(os.path.join(repo, "CLAUDE.md"), claude_md)
    w(os.path.join(repo, "docs", "arch.md"), ("arch %s\n" % CANARY) * 80)
    w(os.path.join(repo, "CLAUDE.local.md"), "local %s\n" % CANARY)
    w(os.path.join(repo, ".claude", "rules", "always.md"), "always %s\n" % CANARY)
    w(os.path.join(repo, ".claude", "rules", "scoped.md"), "---\npaths:\n  - \"src/**/*.ts\"\n---\nscoped %s\n" % CANARY)
    w(os.path.join(repo, ".claudeignore"), "dist/\n*.log\n# comment\n!keep.log\n/secret.txt\n")
    w(os.path.join(repo, ".gitignore"), "dist/\n")
    w(os.path.join(repo, ".mcp.json"), json.dumps({"mcpServers": {"linear": {"command": "x"}}}))
    w(os.path.join(repo, "dist", "bundle.js"), "x" * 300000)
    w(os.path.join(repo, "build", "out.js"), "x" * 150000)
    w(os.path.join(repo, "src", "app.ts"), "code %s\n" % CANARY)
    w(os.path.join(repo, "src", "sub", "CLAUDE.md"), "sub\n" * 100)
    w(os.path.join(repo, "static", "jquery.min.js"), "x" * 30000)
    for i in range(4):
        w(os.path.join(repo, "static", "a%d.js.map" % i), "x" * 10000)
    w(os.path.join(repo, "fixtures", "big.json"), CANARY + "x" * 250000)
    w(os.path.join(repo, "fixtures", "small.json"), "{}")
    w(os.path.join(repo, "package-lock.json"), "x" * 60000)
    w(os.path.join(repo, "logs", "app.log"), "x" * 30000)
    w(os.path.join(repo, "node_modules", "huge", "x.js"), "x" * 900000)
    if with_git:
        git(repo, "init", "-q")
    w(os.path.join(mem_dir(home, repo), "MEMORY.md"), "".join("- mem %d %s\n" % (i, CANARY) for i in range(230)))
    return home, repo


@pytest.fixture
def clean_env(monkeypatch):
    for k in CLEAR_ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


# ---------- end-to-end ----------

@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_full_audit_git_repo(tmp_path):
    home, repo = build_fixture(str(tmp_path))
    code, out, err = audit(repo, home)
    assert code == 0 and err == ""
    lines = out.splitlines()
    assert len(lines) <= 40
    assert CANARY not in out, "audit must never print file contents"
    assert "· git ·" in lines[0]
    assert re.search(r"user\s+~/.claude/CLAUDE.md\s+70 ln .*yellow >60", out)
    assert re.search(r"proj\s+CLAUDE.md\s+\d+ ln .*RED >200", out)
    assert "@docs/arch.md <- CLAUDE.md" in out
    assert "@code.md" not in out and "fenced.md" not in out
    assert "1 @imports not found" in out
    assert re.search(r"rule\s+.claude/rules/always.md .*no paths:", out)
    assert "scoped.md" not in out
    assert "ON DEMAND: 1 subdir CLAUDE.md" in out and "1 path-scoped rules" in out
    assert re.search(r"memory .*MEMORY.md\s+230 ln .*30 ln cut", out)
    assert "comments stripped" in out
    assert "COMPACT: missing" in out and "| ## Compact instructions" in out
    assert ".claudeignore: NO EFFECT (3 entries)" in out
    assert "Read(./**/dist/**)" in out and "Read(./**/*.log)" in out and "Read(/secret.txt)" in out
    assert "READ DENY in effect (2)" in out and "Bash(rm" not in out
    cand = out.split("DENY CANDIDATES", 1)[1].split("GLOB", 1)[0]
    assert "Read(/build/**)" in cand and "Read(./build" not in cand
    assert "dist" not in cand, "gitignored dir must not be a candidate"
    assert "jquery" not in cand and "*.min" not in cand, "already denied by user rule"
    assert "Read(./**/*.map)" in cand
    assert "Read(/fixtures/big.json)" in cand and "small.json" not in cand
    assert "node_modules" not in cand
    assert re.search(r"\[ \] lock .*Read\(/package-lock.json\).*blocks Edit", cand)
    assert re.search(r"\[x\] build", cand)
    assert "GLOB: lists gitignored files" in out and "dist/" in out.split("GLOB", 1)[1]
    assert "MCP servers 2: github, linear" in out
    code2, out2, _ = audit(repo, home)
    assert out2 == out, "output must be deterministic"


def test_no_git_dir(tmp_path):
    home, repo = build_fixture(str(tmp_path), with_git=False)
    code, out, err = audit(repo, home, extra_env={"PATH": "/nonexistent"} if not HAS_GIT else None)
    assert code == 0 and err == ""
    assert "no git" in out.splitlines()[0]
    assert len(out.splitlines()) <= 40 and CANARY not in out
    cand = out.split("DENY CANDIDATES", 1)[1].split("GLOB", 1)[0]
    assert "Read(/build/**)" in cand
    assert "dist" not in cand, "root .gitignore is honoured without git"
    assert "node_modules" not in cand
    assert "GLOB/GREP: no git repo" in out and "node_modules/" in out


def test_no_git_binary_on_path(tmp_path):
    home, repo = build_fixture(str(tmp_path), with_git=False)
    code, out, _ = audit(repo, home, extra_env={"PATH": "/nonexistent"})
    assert code == 0 and "no git" in out and "ALWAYS LOADED" in out


def test_walk_skips_node_modules_and_caps(tmp_path, clean_env):
    clean_env.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "r"
    for i in range(30):
        w(str(repo / "node_modules" / ("p%d" % i) / "index.js"), "x")
        w(str(repo / "src" / ("f%d.py" % i)), "x")
    w(str(repo / "a" / "b" / "c" / "deep.txt"), "x")
    ctx = sa.Ctx(str(repo))
    ctx.git = None
    files, mode, capped = sa.list_files(ctx)
    assert mode == "walk" and not capped
    assert not any("node_modules" in f for f in files)
    assert "a/b/c/deep.txt" in files
    clean_env.setattr(sa, "WALK_ENTRIES", 10)
    files, mode, capped = sa.list_files(ctx)
    assert capped and len(files) <= 10
    data = sa.audit(ctx, deny_only=True)
    assert data["walk_capped"]
    assert "walk capped" in sa.render(ctx, data, deny_only=True)


def test_big_tree_is_fast(tmp_path):
    home = str(tmp_path / "home")
    repo = tmp_path / "big"
    for d in range(60):
        for f in range(60):
            w(str(repo / ("pkg%d" % d) / ("m%d.py" % f)), "x = 1\n")
    for f in range(2000):
        w(str(repo / "node_modules" / ("dep%d" % f) / "i.js"), "x")
    t0 = time.monotonic()
    code, out, _ = audit(str(repo), home)
    assert code == 0 and time.monotonic() - t0 < 10
    if HAS_GIT:
        git(str(repo), "init", "-q")
        t0 = time.monotonic()
        code, out, _ = audit(str(repo), home)
        assert code == 0 and time.monotonic() - t0 < 10 and "· git ·" in out


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_deny_only_and_json(tmp_path):
    home, repo = build_fixture(str(tmp_path))
    code, out, _ = audit(repo, home, "--deny-only")
    assert code == 0 and "deny-only" in out.splitlines()[0]
    assert "ALWAYS LOADED" not in out and "MCP" not in out and "DENY CANDIDATES" in out
    code, out, _ = audit(repo, home, "--json")
    data = json.loads(out)
    assert CANARY not in out
    groups = {c["group"]: c for c in data["candidates"]}
    assert groups["build"]["rules"] == ["Read(/build/**)"]
    assert groups["lock"]["suggested"] is False and groups["build"]["suggested"] is True
    assert data["memory_stats"]["compact"] is False
    assert any(r["kind"] == "import" for r in data["memory"])


def test_unknown_args_and_bad_cwd(tmp_path):
    home = str(tmp_path / "home")
    os.makedirs(home)
    code, out, _ = audit(str(tmp_path), home, "please", "--deny-only --bogus")
    assert code == 0 and "ignored args: please --bogus" in out and "deny-only" in out
    p = subprocess.run([sys.executable, "-I", SCRIPT, "--cwd", str(tmp_path / "nope")], capture_output=True,
                       text=True, env=env_for(home))
    assert p.returncode == 0 and "not a directory" in p.stdout


def test_compact_present_and_comment_free(tmp_path):
    home = str(tmp_path / "home")
    repo = str(tmp_path / "r")
    w(os.path.join(repo, "CLAUDE.md"), "# R\n- rule\n\n## Compact Instructions\n- keep goal\n")
    code, out, _ = audit(repo, home)
    assert "COMPACT: ok" in out and "| ## Compact" not in out


def test_plugins_with_fake_cli(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    os.makedirs(repo)
    w(os.path.join(home, ".claude", "settings.json"), json.dumps({
        "enabledPlugins": {"caveman@caveman": True, "hetz@hetz": True, "gone@x": True, "off@x": False},
        "outputStyle": "Concise",
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}]}}))
    fake = tmp_path / "bin" / "claude"
    w(str(fake), "#!/bin/sh\ncase \"$3\" in\n caveman@caveman) echo '  Always-on:   ~1,204 tok   added';;\n"
                 " hetz@hetz) echo '  Always-on:   ~0 tok   added';;\n *) echo nope; exit 1;;\nesac\n")
    os.chmod(str(fake), 0o755)
    code, out, _ = audit(repo, home, "--plugins", extra_env={"PATH": str(fake.parent) + os.pathsep + os.environ["PATH"]})
    assert code == 0
    assert "caveman@caveman ~1,204 · gone@x ? · hetz@hetz ~0" in out and "PLUGINS always-on tok" in out
    assert "off@x" not in out
    assert "caveman + Concise" in out and "RTK hook + hetz squeeze" in out


# ---------- --add-deny writer ----------

@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_add_deny_local_creates_merges_and_excludes(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    w(os.path.join(repo, "tools", "build", "release.py"), "x\n")
    git(repo, "init", "-q")
    git(repo, "add", "tools")
    code, out, _ = audit(repo, home, "--add-deny", "Read(./build/**)", "Read(/fixtures/big.json)")
    assert code == 0 and "added 2 Read deny rule(s) to .claude/settings.local.json" in out
    assert "note: Read(./build/**) also denies tools/build" in out, "unanchored dir rule names what else it hides"
    path = os.path.join(repo, ".claude", "settings.local.json")
    assert json.load(open(path))["permissions"]["deny"] == ["Read(./build/**)", "Read(/fixtures/big.json)"]
    exclude = open(os.path.join(repo, ".git", "info", "exclude")).read()
    assert "/.claude/settings.local.json*" in exclude.splitlines()
    assert not os.path.exists(path + ".hetz-bak")
    data = json.load(open(path))
    data["permissions"]["allow"] = ["Bash(ls)"]
    data["model"] = "sonnet"
    json.dump(data, open(path, "w"))
    code, out, _ = audit(repo, home, "--add-deny", "Read(./build/**)", "Read(./**/*.map)")
    assert code == 0 and "added 1" in out and "1 already present" in out
    data = json.load(open(path))
    assert data["permissions"]["deny"][-1] == "Read(./**/*.map)" and data["permissions"]["allow"] == ["Bash(ls)"]
    assert data["model"] == "sonnet"
    assert os.path.exists(path + ".hetz-bak"), "untracked file gets a .hetz-bak before modification"
    assert open(os.path.join(repo, ".git", "info", "exclude")).read().count("settings.local.json") == 1
    first_bak = open(path + ".hetz-bak").read()
    code, out, _ = audit(repo, home, "--add-deny", "Read(./build/**)")
    assert code == 0 and "nothing to add" in out
    code, out, _ = audit(repo, home, "--add-deny", "Read(/logs/**)")
    assert code == 0 and "backup settings.local.json.hetz-bak.1" in out
    assert open(path + ".hetz-bak").read() == first_bak, "an earlier backup is never overwritten"
    code, out, _ = audit(repo, home, "--deny-only")
    assert "local 4" in out
    st = subprocess.run(["git", "-C", repo, "status", "--porcelain", "--untracked-files=all"], capture_output=True,
                        text=True, env=env_for(home)).stdout
    assert st == "A  tools/build/release.py\n", "settings + backups stay out of git status"


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_add_deny_from_subdir_excludes_right_path_once(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    sub = os.path.join(repo, "pkg")
    w(os.path.join(sub, "main.py"), "x\n")
    git(repo, "init", "-q")
    for _ in range(3):
        code, out, _ = audit(sub, home, "--add-deny", "Read(/dist/**)")
        assert code == 0
    exclude = open(os.path.join(repo, ".git", "info", "exclude")).read().splitlines()
    assert exclude.count("/pkg/.claude/settings.local.json*") == 1
    st = subprocess.run(["git", "-C", repo, "status", "--porcelain", "--untracked-files=all"], capture_output=True,
                        text=True, env=env_for(home)).stdout
    assert "settings.local.json" not in st and "pkg/main.py" in st


def test_add_deny_refusals_and_project_scope(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    os.makedirs(repo)
    code, out, _ = audit(repo, home, "--add-deny", "Bash(rm -rf /)")
    assert code == 2 and "refused" in out
    code, out, _ = audit(repo, home, "--add-deny")
    assert code == 2
    code, out, _ = audit(repo, home, "--add-deny", "--scope", "team", "Read(./x)")
    assert code == 2
    for blanket in ("Read(**)", "Read(./**)", "Read(//**)", "Read(/**)", "Read(~/**)", "Read(.)", "Read(./*)",
                    "Read(**/*)", "Read(../**)"):
        code, out, _ = audit(repo, home, "--add-deny", "Read(/dist/**)", blanket)
        assert code == 2 and "blanket" in out, blanket
    w(os.path.join(repo, "src", "app.py"), "x\n")
    w(os.path.join(repo, "build", "out.js"), "x\n")
    for hides in ("Read(/src/**)", "Read(./**/*.py)", "Read(//%s/**)" % os.path.realpath(repo).strip("/")):
        code, out, _ = audit(repo, home, "--add-deny", "Read(/dist/**)", hides)
        assert code == 2 and "src/app.py" in out and "nothing written" in out, hides
    assert not os.path.exists(os.path.join(repo, ".claude", "settings.local.json")), "refusal is atomic"
    code, out, _ = audit(repo, home, "--add-deny", "--scope", "project", "Read(/dist/**)", "Read(/build/**)")
    assert code == 0 and ".claude/settings.json" in out
    assert json.load(open(os.path.join(repo, ".claude", "settings.json")))["permissions"]["deny"] == [
        "Read(/dist/**)", "Read(/build/**)"]
    w(os.path.join(repo, ".claude", "settings.local.json"), "{not json")
    code, out, _ = audit(repo, home, "--add-deny", "Read(/dist/**)")
    assert code == 1 and "not a JSON object" in out
    assert open(os.path.join(repo, ".claude", "settings.local.json")).read() == "{not json"


# ---------- memory model units ----------

def test_strip_comments_block_only():
    text = "a\n<!-- gone\nstill gone -->\nb <!-- inline stays -->\n```\n<!-- in fence stays -->\n```\n<!-- x --> tail\n<!-- open\n"
    kept, removed = sa.strip_comments(text)
    assert "gone" not in kept and "inline stays" in kept and "in fence stays" in kept
    assert " tail" in kept and "<!-- open" in kept
    assert removed > 0


def test_find_imports_rules(tmp_path):
    base, home = str(tmp_path), "/h"
    text = ("see @a.md, @./b.md and @~/c.md and @/abs/d.md#frag\n"
            "mail x@y.com `@code.md` @@scoped @#nope\n```\n@fenced.md\n```\n~~~\n@tilde.md\n~~~\n@e\\ f.md\n")
    got = sa.find_imports(text, base, home)
    assert os.path.join(base, "a.md,") in got
    assert os.path.join(base, "b.md") in got
    assert "/h/c.md" in got and "/abs/d.md" in got
    assert os.path.join(base, "e f.md") in got
    joined = " ".join(got)
    for bad in ("y.com", "code.md", "scoped", "nope", "fenced.md", "tilde.md"):
        assert bad not in joined


def test_frontmatter_paths():
    assert sa.fm_paths('paths:\n  - "src/**/*.ts"\n  - lib/**\n') == ["src/**/*.ts", "lib"]
    assert sa.fm_paths("paths: src/**, {a,b}/*.py") == ["src", "{a,b}/*.py"]
    assert sa.fm_paths('paths: ["x/**"]') == ["x"]
    assert sa.fm_paths("paths: \"**\"") == []
    assert sa.fm_paths("description: x") == []
    fm, body = sa.split_frontmatter("---\npaths: a\n---\nbody\n")
    assert fm == "paths: a" and body == "body\n"


def test_mem_truncate_caps():
    text = "".join("line %d\n" % i for i in range(230))
    kept, cut = sa.mem_truncate(text)
    assert kept.count("\n") + 1 == 200 and cut == 30
    long_lines = "\n".join("y" * 300 for _ in range(150))
    kept, cut = sa.mem_truncate(long_lines)
    assert len(kept) <= 25000 and cut == 150 - (kept.count("\n") + 1)
    assert sa.mem_truncate("short\n") == ("short", 0)


def test_import_depth_and_dedupe(tmp_path, clean_env):
    home = str(tmp_path / "home")
    clean_env.setenv("HOME", home)
    repo = str(tmp_path / "r")
    for i in range(7):
        w(os.path.join(repo, "f%d.md" % i), "level %d @f%d.md @f0.md\n" % (i, i + 1))
    w(os.path.join(repo, "CLAUDE.md"), "@f0.md\n")
    ctx = sa.Ctx(repo)
    rows, _, _, _ = sa.collect_memory(ctx)
    names = [os.path.basename(r["path"]) for r in rows]
    assert names == ["CLAUDE.md", "f0.md", "f1.md", "f2.md", "f3.md"], names


def test_external_imports_need_approval(tmp_path, clean_env):
    home = str(tmp_path / "home")
    clean_env.setenv("HOME", home)
    outside = str(tmp_path / "outside.md")
    w(outside, "outside\n")
    repo = str(tmp_path / "r")
    w(os.path.join(repo, "CLAUDE.md"), "@%s\n" % outside)
    rows, _, st, _ = sa.collect_memory(sa.Ctx(repo))
    assert [r["kind"] for r in rows] == ["project"] and st["unapproved"] == 1
    w(os.path.join(home, ".claude", "CLAUDE.md"), "@%s\n" % outside)
    rows, _, st, _ = sa.collect_memory(sa.Ctx(repo))
    assert [r["kind"] for r in rows] == ["user", "import", "project"]
    w(os.path.join(home, ".claude.json"), json.dumps(
        {"projects": {os.path.realpath(repo): {"hasClaudeMdExternalIncludesApproved": True}}}))
    os.remove(os.path.join(home, ".claude", "CLAUDE.md"))
    rows, _, st, _ = sa.collect_memory(sa.Ctx(repo))
    assert [r["kind"] for r in rows] == ["project", "import"]


def test_load_chain_order_and_excludes(tmp_path, clean_env):
    home = str(tmp_path / "home")
    clean_env.setenv("HOME", home)
    top = str(tmp_path / "top")
    repo = os.path.join(top, "repo")
    w(os.path.join(top, "CLAUDE.md"), "ancestor\n")
    w(os.path.join(repo, ".claude", "CLAUDE.md"), "alt\n")
    w(os.path.join(repo, ".claude", "rules", "nested", "deep.md"), "deep\n")
    w(os.path.join(repo, ".claude", "rules", "star.md"), "---\npaths: \"**\"\n---\nstar\n")
    w(os.path.join(repo, "CLAUDE.local.md"), "local\n")
    w(os.path.join(repo, "sub", "CLAUDE.md"), "not at launch\n")
    rows, on_demand, _, _ = sa.collect_memory(sa.Ctx(repo))
    rel = [os.path.relpath(r["path"], os.path.realpath(top)) for r in rows]
    assert rel == ["CLAUDE.md", "repo/.claude/CLAUDE.md", "repo/.claude/rules/nested/deep.md",
                   "repo/.claude/rules/star.md", "repo/CLAUDE.local.md"]
    w(os.path.join(repo, ".claude", "settings.local.json"),
      json.dumps({"claudeMdExcludes": [os.path.realpath(top) + "/CLAUDE.md", "**/rules/nested/**"]}))
    rows, _, st, _ = sa.collect_memory(sa.Ctx(repo))
    assert st["excluded"] == 2 and len(rows) == 3


def test_auto_memory_key_override_and_off(tmp_path, clean_env):
    home = str(tmp_path / "home")
    clean_env.setenv("HOME", home)
    repo = str(tmp_path / "r")
    sub = os.path.join(repo, "pkg")
    os.makedirs(sub)
    if HAS_GIT:
        git(repo, "init", "-q")
        mem = sa.auto_memory(sa.Ctx(sub))
        assert mem["dir"] == mem_dir(home, repo), "auto memory is keyed by the git root"
    w(os.path.join(home, ".claude", "settings.json"), json.dumps({"autoMemoryDirectory": "~/mymem"}))
    assert sa.auto_memory(sa.Ctx(repo))["dir"] == os.path.join(home, "mymem")
    w(os.path.join(repo, ".claude", "settings.json"), json.dumps({"autoMemoryEnabled": False}))
    assert "off" in sa.auto_memory(sa.Ctx(repo))["state"]
    os.remove(os.path.join(repo, ".claude", "settings.json"))
    clean_env.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")
    assert "off" in sa.auto_memory(sa.Ctx(repo))["state"]


def test_pinned_memory_files(tmp_path, clean_env):
    home = str(tmp_path / "home")
    clean_env.setenv("HOME", home)
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    md = mem_dir(home, repo)
    w(os.path.join(md, "MEMORY.md"), "- index\n")
    w(os.path.join(md, "pin.md"), "---\nmetadata:\n  pinned: true\n---\npinned body\n")
    w(os.path.join(md, "other.md"), "---\ntype: note\n---\nbody\n")
    rows, _, _, _ = sa.collect_memory(sa.Ctx(repo))
    assert [r["kind"] for r in rows] == ["memory"], "flag off: MEMORY.md only, pinned files are not injected"
    w(os.path.join(home, ".claude.json"), json.dumps({"cachedGrowthBookFeatures": {"tengu_stone_shell": True}}))
    rows, _, _, mem = sa.collect_memory(sa.Ctx(repo))
    assert [r["kind"] for r in rows] == ["pinned", "index"], "flag on: pinned files + index, no MEMORY.md"
    assert rows[1]["lines"] == 2 and mem["mode"] == "index"


# ---------- deny-rule semantics ----------

def test_deny_matching(tmp_path, clean_env):
    home = str(tmp_path / "home")
    clean_env.setenv("HOME", home)
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    ctx = sa.Ctx(repo)
    cwd = ctx.cwd
    m = sa.rule_matchers(ctx, {"project": ["Read(./dist/**)", "Read(src/gen/**)", "Read(*.snap)"],
                               "user": ["Read(//**/*.min.js)", "Read(~/secrets/**)", "Read(/rel/**)"]})
    yes = ["dist/a.js", "pkg/dist/a.js", "dist", "src/gen/x.ts", "a/b/c.snap", "lib/x.min.js"]
    no = ["distro/a.js", "other/src/gen/x.ts", "src/general.ts", "x.js"]
    for p in yes:
        assert sa.is_denied(m, os.path.join(cwd, p)), p
    for p in no:
        assert not sa.is_denied(m, os.path.join(cwd, p)), p
    assert sa.is_denied(m, os.path.join(ctx.home, "secrets", "k"))
    assert sa.is_denied(m, os.path.join(ctx.config_dir, "rel", "x"))


def test_glob_rx_and_gi_match():
    assert sa.glob_rx("**/*.{js,css}").fullmatch("a/b/c.css")
    assert not sa.glob_rx("*.js").fullmatch("a/b.js")
    assert sa.gi_match("/build", "build/x") and not sa.gi_match("/build", "a/build/x")
    assert sa.gi_match("*.log", "a/b/c.log") and not sa.gi_match("!x", "x")


def test_claudeignore_conversion(tmp_path, clean_env):
    clean_env.setenv("HOME", str(tmp_path / "home"))
    repo = str(tmp_path / "r")
    w(os.path.join(repo, ".claudeignore"), "dist/\n*.log\n/secret.txt\na/b\n\n# c\n!keep\n")
    ci = sa.claudeignore(sa.Ctx(repo))
    assert ci["entries"] == 4
    assert ci["rules"] == ["Read(./**/dist/**)", "Read(./**/*.log)", "Read(/secret.txt)", "Read(/a/b)"]


def test_group_rules():
    items = {"a/x.min.js": [1, 1, False], "b/y.min.js": [2, 1, False], "c/z.min.css": [3, 1, False]}
    assert sa.group_rules("min", items) == ["Read(./**/*.min.css)", "Read(./**/*.min.js)"]
    assert sa.group_rules("map", {"a.map": [5, 1, False]}) == ["Read(/a.map)"]
    assert sa.group_rules("build", {"dist": [1, 1, False], "out": [9, 1, False]}) == ["Read(/out/**)", "Read(/dist/**)"]


def test_never_source_or_config_candidates():
    assert sa.classify(["src", "main.py"]) is None
    assert sa.classify(["fixtures", "helper.py"]) is None
    assert sa.classify(["fixtures", "blob.bin"]) == "data"
    assert sa.classify(["src", "logs", "logger.ts"]) is None
    assert sa.classify(["package-lock.json"]) == "lock"
    assert sa.classify(["x.log.1"]) == "logs"


def test_glob_env_ok(tmp_path):
    home = str(tmp_path / "home")
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    code, out, _ = audit(repo, home, extra_env={"CLAUDE_CODE_GLOB_NO_IGNORE": "false"})
    assert "GLOB: ok" in out
    w(os.path.join(home, ".claude", "settings.json"), json.dumps({"env": {"CLAUDE_CODE_GLOB_NO_IGNORE": "0"}}))
    code, out, _ = audit(repo, home)
    assert "GLOB: ok" in out


def test_mcp_upfront_flags(tmp_path):
    home = str(tmp_path / "home")
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    w(os.path.join(home, ".claude.json"), json.dumps({"mcpServers": {"big": {"alwaysLoad": True}}}))
    code, out, _ = audit(repo, home, extra_env={"ENABLE_TOOL_SEARCH": "false"})
    assert "alwaysLoad: big" in out and "ENABLE_TOOL_SEARCH=false" in out
    code, out, _ = audit(repo, home, extra_env={"ANTHROPIC_BASE_URL": "http://localhost:9999"})
    assert "non-Anthropic ANTHROPIC_BASE_URL" in out


def test_output_line_cap(tmp_path):
    home = str(tmp_path / "home")
    repo = str(tmp_path / "r")
    w(os.path.join(repo, "CLAUDE.md"), "".join("@f%d.md\n" % i for i in range(30)))
    for i in range(30):
        w(os.path.join(repo, "f%d.md" % i), "x\n")
    for g in ("dist", "out", "target", "coverage", "vendor", "generated", ".next", ".nuxt", "build"):
        w(os.path.join(repo, g, "a.bin"), "x" * 120000)
    code, out, _ = audit(repo, home, "--plugins", extra_env={"PATH": "/nonexistent"})
    lines = out.splitlines()
    assert code == 0 and len(lines) <= 40
    assert "+19 more files" in out


# ---------- SKILL.md + template ----------

def _frontmatter(path):
    text = open(path, encoding="utf-8").read()
    fm, body = sa.split_frontmatter(text)
    keys = dict(re.findall(r"^([a-zA-Z-]+):\s*(.*)$", fm, re.M))
    return keys, body


def p3_match(rule, cmd):
    """Claude Code 2.1.272 Bash wildcard rule (P3): escape, '*' -> '.*', a single trailing ' *' is optional."""
    rx = re.escape(rule.strip()).replace(r"\*", ".*")
    if rx.endswith(r"\ .*") and rule.count("*") == 1:
        rx = rx[:-4] + "( .*)?"
    elif rx.endswith(" .*") and rule.count("*") == 1:
        rx = rx[:-3] + "( .*)?"
    norm = lambda x: re.sub(r"[ \t]+", " ", x)
    return re.fullmatch(rx, norm(cmd), re.S) is not None


def test_skill_md_contract():
    keys, body = _frontmatter(SKILL_MD)
    assert keys["name"] == "slim"
    assert keys["disable-model-invocation"] == "true"
    tools = keys["allowed-tools"]
    assert "AskUserQuestion" in tools and "Write(" not in tools and not re.search(r"(^|, )Edit(,|$)", tools)
    pre = re.findall(r"(?:^|(?<=\s))!`([^`]+)`", body, re.M)
    assert pre == ['python3 -I "${CLAUDE_SKILL_DIR}/slim_audit.py"'], "one pre-exec command, no raw $ARGUMENTS"
    assert "$ARGUMENTS" not in body, "Claude Code appends 'ARGUMENTS: ...' only when no placeholder is used"
    assert "ARGUMENTS --deny-only" in body
    bash = re.findall(r"Bash\(([^)]*)\)", tools)
    assert bash == ['python3 -I "${CLAUDE_SKILL_DIR}/slim_audit.py" *'], "anchored to the skill's own script"
    sdir = "/Users/a b/.claude/plugins/cache/hetz/hetz/0.1.0/skills/slim"
    rule = bash[0].replace("${CLAUDE_SKILL_DIR}", sdir)
    cmd = pre[0].replace("${CLAUDE_SKILL_DIR}", sdir)
    for ok in (cmd, cmd + " --backup CLAUDE.md", cmd + " --add-deny --scope local 'Read(/build/**)'"):
        assert p3_match(rule, ok), ok
    for bad in ('python3 -c "import os" ' + sdir + "/slim_audit.py", "python3 " + sdir + "/slim_audit.py",
                'python3 -I "/tmp/evil/slim_audit.py"'):
        assert not p3_match(rule, bad), bad
    assert "--add-deny" in body and "--backup" in body and "${CLAUDE_SKILL_DIR}/slim_audit.py" in body
    assert len(body) / 4 <= 470, "SKILL.md body must stay near the ~350-450 token budget"
    for must in ("KEEP", "SCOPE", "SKILL", "COMMENT", "DOCS", "DELETE", "AskUserQuestion", "/clear",
                 "Self-test", ".hetz-bak", "MEMORY.md"):
        assert must in body, must


def test_template_contract():
    text = open(TEMPLATE, encoding="utf-8").read()
    lines = text.strip().splitlines()
    assert len(lines) == 3
    assert sa.COMPACT_RE.search(text)
    assert lines[1].startswith("- KEEP:") and lines[2].startswith("- DROP:")
    assert sa.read_text(sa.TEMPLATE) == text


def test_logs_rules_use_outermost_logs_dir():
    items = {"Library/Logs/a/b/x.txt": [1, 1, False], "Library/Logs/c.out": [1, 1, False], "app/server.log": [1, 1, False]}
    assert sa.group_rules("logs", items) == ["Read(./**/*.log)", "Read(/Library/Logs/**)"]
    items = {"src/logs/a.txt": [1, 1, False], "src/logs/b.txt": [1, 1, False], "src/logs/c.txt": [1, 1, False]}
    assert sa.group_rules("logs", items, {"src", "src/logs"}) == [
        "Read(/src/logs/a.txt)", "Read(/src/logs/b.txt)", "Read(/src/logs/c.txt)"], "logs dir holding source"


def test_on_demand_excludes_always_loaded_and_pinned_has_no_imports(tmp_path):
    home = str(tmp_path / "home")
    repo = str(tmp_path / "r")
    w(os.path.join(repo, ".claude", "CLAUDE.md"), "alt\n")
    w(os.path.join(repo, "sub", "CLAUDE.md"), "sub\n")
    w(os.path.join(repo, "x.md"), "x\n")
    md = mem_dir(home, repo)
    w(os.path.join(md, "pin.md"), "---\nmetadata:\n  pinned: true\n---\n@%s\n" % os.path.join(repo, "x.md"))
    w(os.path.join(home, ".claude.json"), json.dumps({"cachedGrowthBookFeatures": {"tengu_stone_shell": True}}))
    code, out, _ = audit(repo, home, "--json")
    data = json.loads(out)
    assert [x["path"].endswith("sub/CLAUDE.md") for x in data["on_demand"]] == [True]
    assert [r["kind"] for r in data["memory"]] == ["project", "pinned", "index"]


def test_capped_dir_size_shows_lower_bound(tmp_path, clean_env):
    clean_env.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "r"
    for i in range(30):
        w(str(repo / "dist" / ("f%d.js" % i)), "x" * 10000)
    clean_env.setattr(sa, "DIR_ENTRIES", 5)
    clean_env.setattr(sa, "GROUP_MIN", dict(sa.GROUP_MIN, build=1))
    ctx = sa.Ctx(str(repo))
    ctx.git = None
    data = sa.audit(ctx, deny_only=True)
    assert data["candidates"][0]["capped"] is True
    assert "≥" in sa.render(ctx, data, deny_only=True)


def test_backup_keeps_uncommitted_work_and_never_overwrites(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    w(os.path.join(repo, "CLAUDE.md"), "tracked\n")
    w(os.path.join(repo, "AGENTS.md"), "committed\n")
    w(os.path.join(repo, "CLAUDE.local.md"), "untracked\n")
    if HAS_GIT:
        git(repo, "init", "-q")
        git(repo, "add", "CLAUDE.md", "AGENTS.md")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        w(os.path.join(repo, "CLAUDE.md"), "tracked\nmy uncommitted line\n")
    code, out, _ = audit(repo, home, "--backup", "CLAUDE.md", "AGENTS.md", "CLAUDE.local.md", "nope.md")
    assert code == 0
    assert open(os.path.join(repo, "CLAUDE.local.md.hetz-bak")).read() == "untracked\n"
    assert "not found: nope.md" in out
    if HAS_GIT:
        assert open(os.path.join(repo, "CLAUDE.md.hetz-bak")).read() == "tracked\nmy uncommitted line\n", \
            "tracked file with uncommitted changes must be backed up"
        assert not os.path.exists(os.path.join(repo, "AGENTS.md.hetz-bak"))
        assert "git is the backup): AGENTS.md" in out
        assert "*.hetz-bak*" in open(os.path.join(repo, ".git", "info", "exclude")).read()
    # identical content -> reuse; changed content -> next numbered backup; first backup never overwritten
    code, out, _ = audit(repo, home, "--backup", "CLAUDE.local.md")
    assert "CLAUDE.local.md -> CLAUDE.local.md.hetz-bak" in out
    assert not os.path.exists(os.path.join(repo, "CLAUDE.local.md.hetz-bak.1"))
    w(os.path.join(repo, "CLAUDE.local.md"), "edited by hetz\n")
    code, out, _ = audit(repo, home, "--backup", "CLAUDE.local.md")
    assert "CLAUDE.local.md.hetz-bak.1" in out
    assert open(os.path.join(repo, "CLAUDE.local.md.hetz-bak")).read() == "untracked\n"
    assert open(os.path.join(repo, "CLAUDE.local.md.hetz-bak.1")).read() == "edited by hetz\n"
    code, out, _ = audit(repo, home, "--backup")
    assert code == 2


# ---------- review fixes ----------

@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_rules_are_anchored_and_vendored_code_is_opt_in(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    w(os.path.join(repo, "build", "app.js"), "x" * 150000)
    w(os.path.join(repo, "tools", "build", "release.py"), "print(1)\n")
    w(os.path.join(repo, "src", "generated", "api_types.ts"), "x" * 150000)
    w(os.path.join(repo, "vendor", "lib.go"), "x" * 150000)
    git(repo, "init", "-q")
    code, out, _ = audit(repo, home, "--json")
    groups = {c["group"]: c for c in json.loads(out)["candidates"]}
    assert groups["build"]["rules"] == ["Read(/build/**)"] and groups["build"]["suggested"] is True
    assert set(groups["gen"]["rules"]) == {"Read(/src/generated/**)", "Read(/vendor/**)"}
    assert groups["gen"]["suggested"] is False
    ctx = sa.Ctx(repo)
    m = sa.rule_matchers(ctx, {"local": groups["build"]["rules"]})
    assert sa.is_denied(m, os.path.join(ctx.cwd, "build", "app.js"))
    assert not sa.is_denied(m, os.path.join(ctx.cwd, "tools", "build", "release.py"))
    code, out, _ = audit(repo, home)
    assert re.search(r"\[ \] gen .*may need to read it", out) and re.search(r"\[x\] build", out)


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_subdir_session_sees_main_root_local_settings(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    sub = os.path.join(repo, "pkg")
    w(os.path.join(sub, "build", "a.js"), "x" * 150000)
    w(os.path.join(repo, ".claude", "settings.local.json"), json.dumps({"permissions": {"deny": ["Read(/build/**)"]}}))
    w(os.path.join(sub, ".claude", "settings.local.json"), json.dumps({"permissions": {"deny": ["Read(./x.bin)"]}}))
    git(repo, "init", "-q")
    code, out, _ = audit(sub, home, "--deny-only")
    assert "local 2" in out and "none above thresholds" in out, out


def test_plugins_only(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    os.makedirs(repo)
    code, out, _ = audit(repo, home, "--plugins-only", extra_env={"PATH": "/nonexistent"})
    assert code == 0 and out.strip() == "PLUGINS: claude CLI not on PATH"


def test_memory_index_mode_render(tmp_path):
    home, repo = str(tmp_path / "home"), str(tmp_path / "repo")
    os.makedirs(repo)
    md = mem_dir(home, repo)
    w(os.path.join(md, "MEMORY.md"), "- index %s\n" % CANARY)
    w(os.path.join(md, "a.md"), "---\nname: a\ndescription: %s\n---\nbody\n" % CANARY)
    w(os.path.join(md, "sub", "b.md"), "# heading %s\n" % CANARY)
    w(os.path.join(md, "logs", "skip.md"), "x\n")
    w(os.path.join(home, ".claude.json"), json.dumps({"cachedGrowthBookFeatures": {"tengu_stone_shell": True}}))
    code, out, _ = audit(repo, home)
    assert code == 0 and CANARY not in out
    assert "INDEX of 2 topic files (MEMORY.md not loaded)" in out
    ix = sa.memory_index(md)
    assert ix["files"] == 2 and ix["cut"] == 0 and ix["chars"] > 2 * len(CANARY)
