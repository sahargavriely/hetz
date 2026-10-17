#!/usr/bin/env python3
"""/hetz:handoff preamble: print the state a handoff note needs and keep the note out of git.

Runs from the skill's !`...` block at load time, so its stdout lands in context and saves the
model 3-4 tool calls (each one re-sends the whole late-session context). Contract: stdlib only,
always exit 0, never raise, at most ~40 short lines. Never report a git failure as "0 changed":
say UNKNOWN so the model checks itself.

git(), repo_state(), gitignore_escape() and ensure_excluded() are kept identical to
skills/interview/prep.py (a test enforces it); each script must stay standalone.
"""
import os
import subprocess
import sys
import time

NOTE = (".claude", "handoff.md")
MAX_STATUS = 25
SLOW = 15  # seconds for work-tree scans (status, diff); big repos without fsmonitor are slow


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


def age(path):
    try:
        mins = int((time.time() - os.path.getmtime(path)) // 60)
    except OSError:
        return "?"
    return f"{mins}m" if mins < 120 else f"{mins // 60}h" if mins < 2880 else f"{mins // 1440}d"


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def newest_spec(base):
    d = os.path.join(base, ".claude", "specs")
    try:
        specs = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".md")]
    except OSError:
        return None
    return max(specs, key=mtime) if specs else None


def base_dir():
    """Session project dir: CLAUDE_PROJECT_DIR when present, else the shell cwd."""
    d = os.environ.get("CLAUDE_PROJECT_DIR", "")
    return d if d and os.path.isdir(d) else os.getcwd()


def git_lines(base):
    """Branch, status, diff and log lines; anything git could not read is marked UNKNOWN."""
    rc, branch, _ = git(base, "symbolic-ref", "--short", "-q", "HEAD")
    rc2, head, _ = git(base, "rev-parse", "--short", "-q", "--verify", "HEAD")
    branch = branch if rc == 0 else "detached" if rc == 1 else "UNKNOWN"
    head = head if rc2 == 0 else "no commits" if rc2 == 1 else "UNKNOWN"
    out = [f"branch: {branch} @ {head}"]
    rc, st, err = git(base, "status", "--short", "--untracked-files=normal", timeout=SLOW)
    if rc != 0:
        out.append(f"status: UNKNOWN ({err or 'git status failed'}); run `git status -s` yourself")
    else:
        lines = st.splitlines()
        out.append(f"status: {len(lines)} changed" + (f" (first {MAX_STATUS})" if len(lines) > MAX_STATUS else ""))
        out += lines[:MAX_STATUS]
        if lines and head != "no commits":
            rc, stat, _ = git(base, "diff", "HEAD", "--shortstat", timeout=SLOW)
            if rc != 0 or stat.strip():
                out.append(f"diff vs HEAD: {stat.strip() if rc == 0 else 'UNKNOWN'}")
    if head != "no commits":
        rc, log, err = git(base, "log", "--oneline", "--no-decorate", "-n", "5")
        if rc == 0 and log:
            out += ["log:"] + log.splitlines()
        elif rc != 0:
            out.append(f"log: UNKNOWN ({err or 'git log failed'}); run `git log --oneline -5` yourself")
    return out


def main():
    base = base_dir()
    note = os.path.join(base, *NOTE)
    state = repo_state(base)
    try:
        excluded = ensure_excluded(base, "/".join(NOTE), state)
    except Exception as e:
        excluded = f"not excluded ({type(e).__name__})"
    print(f"note: {note} ({excluded})", flush=True)  # first, so a later failure still leaves the path
    out = []
    if os.path.exists(note):
        out.append(f"previous note: {age(note)} old; overwrite it")
    if state is True:
        out += git_lines(base)
    elif state is False:
        out.append("git: none here")
    else:
        out.append(f"git: UNKNOWN ({state}); run `git status -s` and `git log --oneline -5` yourself")
    spec = newest_spec(base)
    if spec:
        out.append(f"spec: {os.path.relpath(spec, base)} ({age(spec)} old)")
    print("\n".join(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # fail open: the skill must still load
        print(f"state: unavailable ({type(e).__name__})")
    sys.exit(0)
