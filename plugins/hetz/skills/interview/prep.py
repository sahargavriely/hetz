#!/usr/bin/env python3
"""/hetz:interview preamble: free repo facts for triage, and keep .claude/specs/ out of git.

Runs from the skill's !`...` block at load time; its stdout lands in context, so it answers the
cheapest "facts first" questions with zero tool calls. Contract: stdlib only, always exit 0,
never raise, about 12 short lines. A git failure prints UNKNOWN, never "0 uncommitted".

git(), repo_state(), gitignore_escape() and ensure_excluded() are kept identical to
skills/handoff/state.py (a test enforces it); each script must stay standalone.
"""
import json
import os
import re
import subprocess
import sys

SPECS = ".claude/specs/"
MANIFESTS = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "tox.ini", "noxfile.py", "pytest.ini",
    "uv.lock", "poetry.lock", "package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lock",
    "bun.lockb", "deno.json", "tsconfig.json", "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle",
    "build.gradle.kts", "CMakeLists.txt", "Makefile", "justfile", "Dockerfile", "docker-compose.yml",
    "compose.yaml", "mix.exs", "composer.json", "Package.swift", "pubspec.yaml",
)


def git(base, *args, timeout=5):
    """(rc, stdout, first stderr line) of a git command in base; rc None if git can't run, 124 on timeout."""
    try:
        p = subprocess.run(
            ["git", "-C", base, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env={**os.environ, "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
        err = p.stderr.strip().splitlines()
        return p.returncode, p.stdout.rstrip("\n"), (err[0][:160] if err else "")
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except Exception:
        return None, "", ""


def repo_state(base):
    """True in a work tree, False outside one (or no git), else why git could not tell."""
    rc, out, err = git(base, "rev-parse", "--is-inside-work-tree")
    if rc == 0:
        return out == "true"
    if rc is None or "not a git repository" in err:
        return False
    return err or f"git exited {rc}"


def gitignore_escape(path):
    """Make a literal path safe as a gitignore pattern (#, !, globs, backslash, trailing spaces)."""
    out = "".join("\\" + c if c in "[]*?\\" else c for c in path)
    if out[:1] in ("#", "!"):
        out = "\\" + out
    stripped = out.rstrip(" ")
    return stripped + "\\ " * (len(out) - len(stripped))


def ensure_excluded(base, rel, state=None):
    """Keep base/rel out of git via .git/info/exclude (never .gitignore). Returns a status phrase."""
    state = repo_state(base) if state is None else state
    if state is False:
        return "no git repo"
    if state is not True:
        return f"not excluded (git: {state})"
    probe = rel.rstrip("/") + ("/hetz-probe.md" if rel.endswith("/") else "")
    if git(base, "ls-files", "--", rel.rstrip("/"))[1]:
        return "tracked by git"
    if git(base, "check-ignore", "-q", "--no-index", "--", probe)[0] == 0:
        return "kept out of git"
    rc, exclude, _ = git(base, "rev-parse", "--git-path", "info/exclude")
    prc, prefix, _ = git(base, "rev-parse", "--show-prefix")
    if rc != 0 or prc != 0 or not exclude:
        return "not excluded (no .git/info/exclude)"
    if "\n" in prefix or "\r" in prefix:
        return "not excluded (path has a line break)"
    path = os.path.join(base, exclude)
    pattern = gitignore_escape(prefix + rel)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                old = f.read()
        if pattern not in old.splitlines():
            with open(path, "a", encoding="utf-8") as f:
                f.write(("" if not old or old.endswith("\n") else "\n") + pattern + "\n")
    except OSError:
        return "not excluded (exclude file not writable)"
    if git(base, "check-ignore", "-q", "--no-index", "--", probe)[0] != 0:
        return "not excluded (pattern did not match)"
    return "kept out of git"


def base_dir():
    """Session project dir: CLAUDE_PROJECT_DIR when present, else the shell cwd."""
    d = os.environ.get("CLAUDE_PROJECT_DIR", "")
    return d if d and os.path.isdir(d) else os.getcwd()


def clip(items, n):
    items = list(items)
    return ", ".join(items[:n]) + (f", +{len(items) - n} more" if len(items) > n else "")


def read(path, limit=200_000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def build_hints(base, present):
    """Runnable entry points worth knowing before asking anything."""
    hints = []
    if "package.json" in present:
        try:
            scripts = json.loads(read(os.path.join(base, "package.json"))).get("scripts") or {}
            if scripts:
                hints.append("npm scripts: " + clip(scripts, 10))
        except Exception:
            pass
    for name in ("Makefile", "justfile"):
        if name in present:
            targets = re.findall(r"^([A-Za-z0-9][\w.-]*)\s*:(?!=)", read(os.path.join(base, name)), re.M)
            targets = [t for t in dict.fromkeys(targets) if not t.startswith(".")]
            if targets:
                hints.append(f"{name}: " + clip(targets, 10))
    if "pyproject.toml" in present:
        tools = re.findall(r"^\[tool\.([\w-]+)", read(os.path.join(base, "pyproject.toml")), re.M)
        if tools:
            hints.append("pyproject tools: " + clip(dict.fromkeys(tools), 8))
    wf = os.path.join(base, ".github", "workflows")
    if os.path.isdir(wf):
        hints.append("CI: " + clip(sorted(os.listdir(wf)), 6))
    return hints


def main():
    base = base_dir()
    out = []
    state = repo_state(base)
    if state is True:
        top = git(base, "rev-parse", "--show-toplevel")[1] or base
        rc, branch, _ = git(base, "symbolic-ref", "--short", "-q", "HEAD")
        branch = branch if rc == 0 else "detached" if rc == 1 else "UNKNOWN"
        rc, st, _ = git(base, "status", "--short", timeout=15)
        dirty = f"{len(st.splitlines())} uncommitted" if rc == 0 else "uncommitted UNKNOWN"
        sub = "" if os.path.realpath(top) == os.path.realpath(base) else f", cwd {os.path.relpath(base, top)}"
        out.append(f"repo: {top} (branch {branch}, {dirty}{sub})")
        rc, log, _ = git(base, "log", "--oneline", "--no-decorate", "-n", "5")
        if rc == 0 and log:
            out.append("recent commits:")
            out += ["  " + line[:100] for line in log.splitlines()]
    elif state is False:
        out.append(f"dir: {base} (no git)")
    else:
        out.append(f"dir: {base} (git UNKNOWN: {state})")
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name.lower())
    except OSError:
        entries = []
    names = [e.name + ("/" if e.is_dir() else "") for e in entries if not e.name.startswith(".")]
    present = {e.name for e in entries}
    if names:
        out.append("top level: " + clip(names, 30))
    found = [m for m in MANIFESTS if m in present]
    if found:
        out.append("manifests: " + ", ".join(found))
    out += build_hints(base, present)
    specs_dir = os.path.join(base, *SPECS.strip("/").split("/"))
    try:
        existing = sorted(n for n in os.listdir(specs_dir) if n.endswith(".md"))
    except OSError:
        existing = []
    line = f"specs dir: {specs_dir}/ ({ensure_excluded(base, SPECS, state)})"
    print("\n".join(out + [line + (f"; existing: {clip(existing, 8)}" if existing else "")]))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # fail open: the skill must still load
        print(f"repo facts: unavailable ({type(e).__name__})")
    sys.exit(0)
