#!/usr/bin/env python3
"""hetz settings manager behind /hetz:setup and scripts/uninstall.sh (Python 3 stdlib only).

  status [--json]                                   state, conflicts, and the setup question to ask
  apply --scope user|project|local --items a,b [--deny RULE]... [--force-keys k,..] [--dry-run] [--json]
  undo --scope user|project|local|all [--force] [--dry-run] [--json]
  deny-scan [--json]                                per-repo Read-deny candidates (REPO mode)

Global: --home DIR (or $HETZ_HOME) stands in for ~ so tests never touch the real ~/.claude;
--project DIR (default cwd) for project/local scope; --shell fish|zsh|bash; --auth api|subscription.

Merge rules: dicts merge; arrays are unioned; an existing value that differs (env values included) is
reported and kept unless its dotted key (or file:<name>) is in --force-keys. Every write backs the file up
as <file>.hetz-<ts>.bak (beside ~/.claude files; repo and other files under ~/.claude/hetz/backups/) and
records each change with its prior value in <home>/.claude/hetz/manifest.json, so undo restores exactly
what was there. Settings files are re-read immediately before writing.
"""
import argparse
import copy
import datetime
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
PROFILES_DIR = os.path.join(PLUGIN_ROOT, "profiles")
TEMPLATES_DIR = os.path.join(PLUGIN_ROOT, "templates")
STATUSLINE_SRC = os.path.join(HERE, "statusline.py")
MIN_VERSION = (2, 1, 272)
SCOPES = ("user", "project", "local")
ATOMIC_KEYS = {"statusLine"}
FILE_ITEMS = {"statusline", "statusline-wrap", "explore", "launchers"}  # file kinds; user scope only
LAUNCHERS = ("cc", "cc-deep", "cc-quick", "cc-spec", "cc-do")
MISSING = object()
RULE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\(.+\))?$")


class SetupError(Exception):
    pass


# ---------------------------------------------------------------- paths and files

class Paths:
    def __init__(self, home=None, project=None):
        override = home or os.environ.get("HETZ_HOME")
        self.overridden = bool(override)
        self.home = os.path.abspath(os.path.expanduser(override)) if override else os.path.expanduser("~")
        self.claude = os.path.join(self.home, ".claude")
        self.hetz = os.path.join(self.claude, "hetz")
        self.manifest = os.path.join(self.hetz, "manifest.json")
        self.project = os.path.abspath(project or os.getcwd())
        xdg = None if self.overridden else os.environ.get("XDG_CONFIG_HOME")
        self.config = xdg or os.path.join(self.home, ".config")

    def settings(self, scope):
        if scope == "user":
            return os.path.join(self.claude, "settings.json")
        name = "settings.json" if scope == "project" else "settings.local.json"
        return os.path.join(self.project, ".claude", name)

    def tilde(self, path):
        """Short display path: ./rel inside the project, ~/rel inside home."""
        if path == self.project:
            return "."
        if self.project != self.home and path.startswith(self.project + os.sep):
            return "." + path[len(self.project):]
        if path == self.home or path.startswith(self.home + os.sep):
            return "~" + path[len(self.home):]
        return path


def read_json_file(path):
    """(data, raw): ({}, None) when absent; SetupError when unreadable or not a JSON object."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        raise SetupError("cannot read %s: %s" % (path, e))
    if not raw.strip():
        return {}, raw
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SetupError("%s is not valid JSON (%s); fix it first, nothing was changed" % (path, e))
    if not isinstance(data, dict):
        raise SetupError("%s is not a JSON object; nothing was changed" % path)
    return data, raw


def safe_read(path):
    try:
        return read_json_file(path)[0], None
    except SetupError as e:
        return {}, str(e)


def dumps(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def write_atomic(path, text, mode=None):
    """Write via temp + rename on the symlink target, so dotfile-managed symlinks survive."""
    real = os.path.realpath(path)
    os.makedirs(os.path.dirname(real), exist_ok=True)
    if mode is None and os.path.exists(real):
        mode = stat.S_IMODE(os.stat(real).st_mode)
    tmp = "%s.hetz-tmp-%d" % (real, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, real)


def makedirs_recorded(path):
    """os.makedirs that returns the directories it created, deepest last."""
    made = []
    cur = os.path.abspath(path)
    while not os.path.isdir(cur):
        made.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    os.makedirs(path, exist_ok=True)
    return list(reversed(made))


def stamp():
    return datetime.datetime.now().strftime("%Y%m%dT%H%M%S")


def backup_dir(paths, path, external):
    """Beside the file for ~/.claude files; repo (project/local) settings and files outside ~/.claude go to
    ~/.claude/hetz/backups/<hash>/ so a copy (maybe holding secrets) never lands untracked in a git work tree."""
    path = os.path.abspath(path)
    if not external and path.startswith(paths.claude + os.sep):
        return os.path.dirname(path)
    return os.path.join(paths.hetz, "backups", hashlib.sha1(path.encode("utf-8")).hexdigest()[:12])


def backup_path(path, ts, folder=None):
    base = os.path.join(folder or os.path.dirname(os.path.abspath(path)), os.path.basename(path))
    candidate, n = "%s.hetz-%s.bak" % (base, ts), 1
    while os.path.exists(candidate):
        n += 1
        candidate = "%s.hetz-%s-%d.bak" % (base, ts, n)
    return candidate


def make_backup(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(os.path.realpath(src), dst)
    return dst


def short(value, limit=60):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------- key paths and merge

def get_path(doc, path, default=MISSING):
    cur = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def set_path(doc, path, value):
    cur = doc
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = cur[key] = {}
        cur = nxt
    cur[path[-1]] = value


def del_path(doc, path):
    parent = get_path(doc, path[:-1]) if len(path) > 1 else doc
    if isinstance(parent, dict) and path[-1] in parent:
        del parent[path[-1]]
        return True
    return False


def dedupe(values):
    out = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def merge_desired(target, extra):
    """Combine item settings into one desired dict (lists concatenate, dicts merge, later scalars win)."""
    for key, val in extra.items():
        have = target.get(key, MISSING)
        if isinstance(have, dict) and isinstance(val, dict):
            merge_desired(have, val)
        elif isinstance(have, list) and isinstance(val, list):
            target[key] = dedupe(have + val)
        else:
            target[key] = copy.deepcopy(val)
    return target


def plan_settings(current, desired, force=(), replaces=()):
    """Key-level ops merging `desired` into `current` (not mutated).

    ops: create (container), add, union (array elements), replace (forced), same, conflict.
    """
    ops = []

    def walk(cur, want, path):
        for key, val in want.items():
            p = path + [key]
            dotted = ".".join(p)
            atomic = dotted in ATOMIC_KEYS
            if not isinstance(cur, dict) or key not in cur:
                if isinstance(val, dict) and val and not atomic:
                    ops.append({"op": "create", "path": p, "type": "dict"})
                    walk({}, val, p)
                elif isinstance(val, list) and not atomic:
                    ops.append({"op": "create", "path": p, "type": "list"})
                    if val:
                        ops.append({"op": "union", "path": p, "added": dedupe(val)})
                else:
                    ops.append({"op": "add", "path": p, "value": val})
                continue
            have = cur[key]
            if not atomic and isinstance(val, dict) and isinstance(have, dict):
                walk(have, val, p)
            elif not atomic and isinstance(val, list) and isinstance(have, list):
                added = [x for x in dedupe(val) if x not in have]
                ops.append({"op": "union", "path": p, "added": added} if added else {"op": "same", "path": p, "value": val})
            elif have == val:
                ops.append({"op": "same", "path": p, "value": val})
            elif dotted in force or dotted in replaces:
                ops.append({"op": "replace", "path": p, "value": val, "prior": have})
            else:
                ops.append({"op": "conflict", "path": p, "value": val, "prior": have})

    walk(current, desired, [])
    return ops


CHANGE_OPS = ("create", "add", "union", "replace")


def apply_ops(doc, ops):
    for op in ops:
        kind, path = op["op"], op["path"]
        if kind == "create":
            set_path(doc, path, {} if op["type"] == "dict" else [])
        elif kind in ("add", "replace"):
            set_path(doc, path, copy.deepcopy(op["value"]))
        elif kind == "union":
            get_path(doc, path).extend(copy.deepcopy(op["added"]))
    return doc


def undo_ops(doc, ops, force=False):
    """Reverse recorded ops on doc in place; returns (mark, key, detail) notes."""
    notes = []
    for op in reversed(ops):
        kind, path = op["op"], op["path"]
        key = ".".join(path)
        cur = get_path(doc, path)
        if kind == "add":
            if cur is MISSING:
                continue
            if cur == op["value"] or force:
                del_path(doc, path)
                notes.append(("-", key, short(op["value"])))
            else:
                notes.append(("!", key, "changed since apply (now %s); kept" % short(cur)))
        elif kind == "replace":
            if cur == op["value"] or force:
                set_path(doc, path, copy.deepcopy(op["prior"]))
                notes.append(("<", key, "restored %s" % short(op["prior"])))
            else:
                notes.append(("!", key, "changed since apply (now %s); kept" % short(cur)))
        elif kind == "union":
            if isinstance(cur, list):
                for item in op["added"]:
                    if item in cur:
                        cur.remove(item)
                        notes.append(("-", key, short(item)))
        elif kind == "create":
            if cur == {} or cur == []:
                del_path(doc, path)
            elif cur is not MISSING:
                notes.append(("!", key, "not empty; kept"))
    return notes


# ---------------------------------------------------------------- environment

def parse_version(text):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def claude_version():
    exe = shutil.which("claude")
    if not exe:
        return None
    real = os.path.basename(os.path.realpath(exe))
    if re.fullmatch(r"\d+\.\d+\.\d+", real):
        return real
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    v = parse_version(out)
    return ".".join(map(str, v)) if v else None


def detect_auth(paths, user_settings, override=None):
    if override:
        return override
    if any(os.environ.get(k) for k in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")):
        return "cloud"
    env = user_settings.get("env") if isinstance(user_settings.get("env"), dict) else {}
    if os.environ.get("ANTHROPIC_API_KEY") or user_settings.get("apiKeyHelper") or env.get("ANTHROPIC_API_KEY"):
        return "api"
    data, _ = safe_read(os.path.join(paths.home, ".claude.json"))
    if data.get("oauthAccount"):
        return "subscription"
    return "unknown"


def login_shell(override=None):
    if override:
        return os.path.basename(override)
    shell = ""
    try:
        shell = pwd.getpwuid(os.getuid()).pw_shell
    except Exception:
        pass
    return os.path.basename(shell or os.environ.get("SHELL", "")) or "unknown"


def statusline_command(paths, wrap=False):
    """A shell-neutral command (valid in sh, bash, zsh, fish) that runs the installed statusline with the
    interpreter recorded in ~/.claude/hetz/py, skipping pyenv-shim latency."""
    script = os.path.join(paths.hetz, "statusline.py")
    py = os.path.join(paths.hetz, "py")
    flag = " --wrap" if wrap else ""
    if any(c in script for c in "'\"$`\\"):
        return 'python3 -I "%s"%s' % (script, flag)
    return "sh -c 'P=$(cat \"%s\" 2>/dev/null); [ -x \"$P\" ] || P=python3; exec \"$P\" -I \"%s\"%s'" % (py, script, flag)


def is_hetz_statusline(value):
    return isinstance(value, dict) and "hetz/statusline.py" in str(value.get("command", ""))


def environment(paths, user_settings, args):
    sl = user_settings.get("statusLine")
    auth = detect_auth(paths, user_settings, getattr(args, "auth", None))
    foreign = bool(sl) and not is_hetz_statusline(sl)
    return {
        "auth": auth,
        "api_auth": auth == "api",
        "foreign_statusline": foreign,
        "wrap_mode": foreign or (is_hetz_statusline(sl) and "--wrap" in str(sl.get("command", ""))),
        "shell": login_shell(getattr(args, "shell", None)),
        "ctx": {
            "idle_minutes": "5" if auth in ("api", "cloud") else "60",
            "statusline_command": statusline_command(paths),
            "statusline_wrap_command": statusline_command(paths, wrap=True),
        },
    }


def render(value, ctx):
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if isinstance(value, str):
        return re.sub(r"\{([a-z_]+)\}", lambda m: ctx.get(m.group(1), m.group(0)), value)
    return value


def item_settings(item, env, user_settings):
    out = render(item.get("settings", {}), env["ctx"])
    sl, want = user_settings.get("statusLine"), out.get("statusLine")
    if item.get("id") == "statusline-wrap" and isinstance(sl, dict) and isinstance(want, dict):
        merged = dict(sl)  # keep padding / refreshInterval / hideVimModeIndicator of the wrapped line
        merged.update(want)
        out["statusLine"] = merged
    return out


# ---------------------------------------------------------------- items and file plans

def load_items():
    items = {}
    for name, default in (("safe.json", True), ("optional.json", False)):
        with open(os.path.join(PROFILES_DIR, name), encoding="utf-8") as f:
            data = json.load(f)
        for iid, spec in data.get("items", {}).items():
            item = dict(spec)
            item.setdefault("default", default)
            item["id"] = iid
            item["profile"] = name
            items[iid] = item
    return items


def visible(item, env):
    req, hide = item.get("requires"), item.get("hide_if")
    return (not req or env.get(req)) and not (hide and env.get(hide))


def launcher_files(paths, shell):
    """(dst, src, force_key) for the login shell, plus notes."""
    src_dir = os.path.join(TEMPLATES_DIR, "launchers")
    if shell == "fish":
        fdir = os.path.join(paths.config, "fish", "functions")
        return [(os.path.join(fdir, n + ".fish"), os.path.join(src_dir, "fish", n + ".fish"), "file:%s.fish" % n) for n in LAUNCHERS], []
    if shell in ("zsh", "bash"):
        dst = os.path.join(paths.hetz, "launchers." + shell)
        rc = "~/.zshrc" if shell == "zsh" else "~/.bashrc"
        return [(dst, os.path.join(src_dir, "hetz." + shell), "file:launchers." + shell)], [
            "add `source %s` to %s (hetz never edits shell rc files)" % (paths.tilde(dst), rc)]
    return [], ["no launcher template for shell %r; pass --shell fish|zsh|bash" % shell]


def manifest_owned(manifest):
    owned = set()
    for entry in manifest.get("entries", []):
        for fop in entry.get("files", []):
            owned.add(fop.get("path"))
    return owned


def plan_files(paths, selected, env, user_settings, force, manifest, with_py):
    """File ops: dicts with dst, content, mode, state (create|same|update|replace|conflict), force_key, notes."""
    wanted, notes = [], []
    for item in selected:
        for kind in item.get("files", []):
            if kind == "statusline":
                wanted.append((os.path.join(paths.hetz, "statusline.py"), read_text(STATUSLINE_SRC), 0o755, None))
            elif kind == "statusline-wrap":
                sl = user_settings.get("statusLine")
                if isinstance(sl, dict) and not is_hetz_statusline(sl):
                    cfg = {"command": sl.get("command", ""), "_doc": "previous statusLine, wrapped by hetz; undo restores it"}
                    wanted.append((os.path.join(paths.hetz, "statusline-wrap.json"), dumps(cfg), 0o644, None))
            elif kind == "explore":
                wanted.append((os.path.join(paths.claude, "agents", "Explore.md"), read_text(os.path.join(TEMPLATES_DIR, "Explore.md")), 0o644, "file:Explore.md"))
            elif kind == "launchers":
                files, extra = launcher_files(paths, env["shell"])
                notes.extend(extra)
                for dst, src, key in files:
                    wanted.append((dst, read_text(src), 0o644, key))
                    name = os.path.basename(dst).rsplit(".", 1)[0]
                    if name in LAUNCHERS:
                        hit = shutil.which(name)
                        if hit:
                            notes.append("%s shadows %s when typed in your shell" % (name, hit))
                if env["shell"] in ("zsh", "bash"):
                    for name in LAUNCHERS:
                        hit = shutil.which(name)
                        if hit:
                            notes.append("%s shadows %s once sourced" % (name, hit))
    if with_py:
        py = os.path.join(paths.hetz, "py")
        if not os.path.exists(py):
            wanted.append((py, os.path.realpath(sys.executable) + "\n", 0o644, None))
    owned = manifest_owned(manifest)
    plans, seen = [], set()
    for dst, content, mode, force_key in wanted:
        if dst in seen or content is None:
            continue
        seen.add(dst)
        have = read_text(dst) if os.path.lexists(dst) else None
        if not os.path.lexists(dst):
            state = "create"
        elif have == content:
            state = "same"
        elif dst.startswith(paths.hetz + os.sep) or dst in owned:
            state = "update"
        elif force_key and force_key in force:
            state = "replace"
        else:
            state = "conflict"
        plans.append({"dst": dst, "content": content, "mode": mode, "state": state, "force_key": force_key,
                      "backup_dir": backup_dir(paths, dst, False)})
    return plans, dedupe(notes)


def git(project, *argv):
    try:
        proc = subprocess.run(["git", "-C", project] + list(argv), capture_output=True, text=True, timeout=10)
        return proc.returncode, proc.stdout
    except Exception:
        return 1, ""


def plan_exclude(paths, scope):
    """Local settings must stay out of git: add to .git/info/exclude (never .gitignore) when not ignored."""
    if scope != "local":
        return []
    rel = ".claude/settings.local.json"
    code, _ = git(paths.project, "check-ignore", "-q", rel)
    if code == 0:
        return []
    code, out = git(paths.project, "rev-parse", "--git-path", "info/exclude")
    if code != 0 or not out.strip():
        return []
    exclude = out.strip()
    if not os.path.isabs(exclude):
        exclude = os.path.join(paths.project, exclude)
    return [{"op": "exclude_add", "path": exclude, "line": rel}]


# ---------------------------------------------------------------- manifest

def load_manifest(paths):
    data, err = safe_read(paths.manifest)
    if err:
        raise SetupError("manifest unreadable (%s); not touching anything" % err)
    data.setdefault("version", 1)
    data.setdefault("entries", [])
    return data


def save_manifest(paths, manifest):
    if manifest.get("entries"):
        write_atomic(paths.manifest, dumps(manifest))
    elif os.path.exists(paths.manifest):
        os.remove(paths.manifest)


# ---------------------------------------------------------------- reporting helpers

def describe_ops(ops, paths=None):
    lines = []
    for op in ops:
        key = ".".join(op["path"])
        kind = op["op"]
        if kind == "add":
            lines.append("  + %s = %s" % (key, short(op["value"])))
        elif kind == "union":
            lines.extend("  + %s += %s" % (key, short(x)) for x in op["added"])
        elif kind == "replace":
            lines.append("  ~ %s = %s (was %s)" % (key, short(op["value"]), short(op["prior"])))
        elif kind == "same":
            lines.append("  = %s already set" % key)
        elif kind == "conflict":
            lines.append("  ! %s: keeping %s (hetz wants %s); add --force-keys %s to replace" % (key, short(op["prior"]), short(op["value"]), key))
    return lines


def describe_files(plans, paths):
    marks = {"create": "+", "same": "=", "update": "~", "replace": "~"}
    lines = []
    for p in plans:
        dst = paths.tilde(p["dst"])
        if p["state"] == "conflict":
            lines.append("  ! %s exists and differs; keeping it; add --force-keys %s to replace (backed up)" % (dst, p["force_key"]))
        else:
            label = {"create": "", "same": " (unchanged)", "update": " (update, backed up)", "replace": " (replace yours, backed up)"}[p["state"]]
            lines.append("  %s %s%s" % (marks[p["state"]], dst, label))
    return lines


# ---------------------------------------------------------------- status

def chunk(seq, size=4):
    n = len(seq)
    if not n:
        return []
    k = -(-n // size)
    base, extra = divmod(n, k)
    out, i = [], 0
    for j in range(k):
        step = base + (1 if j < extra else 0)
        out.append(seq[i:i + step])
        i += step
    return out


def item_state(item, paths, env, user, manifest):
    rendered = item_settings(item, env, user)
    ops = plan_settings(user, rendered, replaces=item.get("replaces", ()))
    files, _ = plan_files(paths, [item], env, user, (), manifest, False)
    kinds = {op["op"] for op in ops} | {"file_" + f["state"] for f in files}
    if not kinds:
        return "off"
    pending = kinds - {"same", "file_same", "conflict", "file_conflict"}
    if kinds & {"conflict", "file_conflict"}:
        # Conflicting keys are kept; the rest still applies, so such an item stays recommended.
        return "partial-conflict" if pending else "conflict"
    if not pending:
        return "on"
    return "partial" if kinds & {"same", "file_same"} else "off"


def gather_status(paths, args):
    user, user_err = safe_read(paths.settings("user"))
    project, project_err = safe_read(paths.settings("project"))
    local, local_err = safe_read(paths.settings("local"))
    manifest, manifest_err = {"entries": []}, None
    try:
        manifest = load_manifest(paths)
    except SetupError as e:
        manifest_err = str(e)
    env = environment(paths, user, args)
    version = claude_version()
    notes = []
    for err in (user_err, project_err, local_err, manifest_err):
        if err:
            notes.append("ERROR " + err)
    ver = parse_version(version)
    if ver and ver < MIN_VERSION:
        notes.append("claude %s is older than %s: autoCompactWindow/bashOutputMaxChars may be ignored" % (version, ".".join(map(str, MIN_VERSION))))
    style = user.get("outputStyle")
    if style and style != "Concise":
        notes.append("outputStyle is %s: concise keeps it unless forced" % short(style))
    if env["foreign_statusline"]:
        notes.append("statusLine already set: offering statusline-wrap (keeps yours, appends hetz)")
    for scope, data in (("user", user), ("project", project), ("local", local)):
        if re.search(r"\brtk\b", json.dumps(data.get("hooks", {}))):
            notes.append("rtk hook in %s settings: squeeze skips rtk commands; set rtk exclude_commands for git diff, cat, ls; never both on one command" % scope)
        plugins = data.get("enabledPlugins") if isinstance(data.get("enabledPlugins"), dict) else {}
        for name, on in plugins.items():
            if "caveman" in name.lower() and on:
                notes.append("%s enabled (%s): ~1.2k tokens + per-turn reminder; Concise covers it, suggest disabling" % (name, scope))
    senv = user.get("env") if isinstance(user.get("env"), dict) else {}
    if os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") or senv.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
        notes.append("CLAUDE_CODE_AUTO_COMPACT_WINDOW is set and overrides the cap item")
    if os.environ.get("CLAUDE_CONFIG_DIR") and not paths.overridden:
        notes.append("CLAUDE_CONFIG_DIR is set; hetz manages %s only" % paths.tilde(paths.claude))
    items = load_items()
    states = {}
    for iid, item in items.items():
        if not visible(item, env):
            continue
        state = item_state(item, paths, env, user, manifest)
        states[iid] = state
        if iid == "explore" and state == "conflict":
            notes.append("~/.claude/agents/Explore.md is yours: explore keeps it unless forced (file:Explore.md)")
    ask_ids = [i for i in states if states[i] != "on"]
    rec = [i for i in ask_ids if items[i]["default"] and states[i] != "conflict"]
    opt = [i for i in ask_ids if i not in rec]
    groups = [("Core", g) for g in chunk(rec)] + [("Optional", g) for g in chunk(opt)]
    if any(len(g) < 2 for _, g in groups) or len(groups) > 4:
        groups = [("Setup", g) for g in chunk(rec + opt)][:4]
    entries = manifest.get("entries", [])
    return {
        "home": paths.home,
        "claude_dir": paths.claude,
        "claude_version": version,
        "version_ok": bool(ver and ver >= MIN_VERSION),
        "auth": env["auth"],
        "login_shell": env["shell"],
        "python": os.path.realpath(sys.executable),
        "py_file": (read_text(os.path.join(paths.hetz, "py")) or "").strip() or None,
        "settings": {
            "user": {"file": paths.settings("user"), "exists": os.path.exists(paths.settings("user")), "keys": sorted(user)},
            "project": {"file": paths.settings("project"), "exists": os.path.exists(paths.settings("project")), "keys": sorted(project)},
            "local": {"file": paths.settings("local"), "exists": os.path.exists(paths.settings("local")), "keys": sorted(local)},
        },
        "notes": notes,
        "items": {iid: {"state": st, "label": items[iid]["label"], "why": items[iid].get("why", ""),
                        "recommended": bool(items[iid]["default"]) and st != "conflict"}
                  for iid, st in states.items()},
        "ask": [{"header": h, "items": g} for h, g in groups],
        "manifest": [{"ts": e.get("ts"), "scope": e.get("scope"), "file": e.get("file"), "items": e.get("items", []),
                      "deny": e.get("deny", []), "changes": len(e.get("ops", [])) + len(e.get("files", []))} for e in entries],
    }


def print_status(st, paths):
    out = ["hetz setup status",
           "claude %s%s · auth %s · login shell %s · python %s" % (
               st["claude_version"] or "?", "" if st["version_ok"] else " (needs >= 2.1.272)", st["auth"], st["login_shell"], st["python"])]
    us = st["settings"]["user"]
    out.append("user settings %s: %s" % (paths.tilde(us["file"]), ("%d keys" % len(us["keys"])) if us["exists"] else "absent"))
    if st["manifest"]:
        out.append("manifest (%s):" % paths.tilde(paths.manifest))
        for e in st["manifest"]:
            what = ",".join(e["items"] + ["deny:%d" % len(e["deny"])] * bool(e["deny"]))
            out.append("  %s %s %s: %s (%d changes)" % (e["ts"], e["scope"], paths.tilde(e["file"]), what, e["changes"]))
    else:
        out.append("manifest: none (nothing applied by hetz)")
    if st["notes"]:
        out.append("notes:")
        out.extend("  ! " + n for n in st["notes"])
    on = [i for i, v in st["items"].items() if v["state"] == "on"]
    out.append("on: " + (", ".join(on) if on else "(none)"))
    if st["ask"]:
        out.append("ask: one multiSelect question per line; option = id, * = (Recommended)")
        for g in st["ask"]:
            opts = ["%s%s — %s%s" % (i, "*" if st["items"][i]["recommended"] else "", st["items"][i]["label"],
                                      " [%s]" % st["items"][i]["state"] if st["items"][i]["state"] != "off" else "") for i in g["items"]]
            out.append("  %s: %s" % (g["header"], " | ".join(opts)))
    else:
        out.append("ask: nothing left to offer")
    print("\n".join(out))


# ---------------------------------------------------------------- apply

def split_csv(values):
    out = []
    for v in values or []:
        out.extend(x.strip() for x in v.split(",") if x.strip())
    return dedupe(out)


def build_request(args, paths, user_settings, items):
    ids = split_csv(args.items)
    unknown = [i for i in ids if i not in items]
    if unknown:
        raise SetupError("unknown item(s): %s (known: %s)" % (", ".join(unknown), ", ".join(items)))
    if args.scope != "user":
        bad = [i for i in ids if items[i].get("files")]
        if bad:
            raise SetupError("%s only apply at --scope user" % ", ".join(bad))
    deny = dedupe(args.deny or [])
    bad_rules = [r for r in deny if not RULE_RE.match(r)]
    if bad_rules:
        raise SetupError("not a permission rule: %s (expected e.g. Read(/dist/**))" % ", ".join(bad_rules))
    if not ids and not deny:
        raise SetupError("nothing to apply: pass --items and/or --deny")
    env = environment(paths, user_settings, args)
    # `requires` gates applicability; `hide_if` only trims what status offers, so an explicit request still runs.
    skipped = [i for i in ids if items[i].get("requires") and not env.get(items[i]["requires"])]
    selected = [items[i] for i in ids if i not in skipped]
    desired, replaces = {}, []
    for item in selected:
        merge_desired(desired, item_settings(item, env, user_settings))
        replaces.extend(item.get("replaces", []))
    if deny:
        merge_desired(desired, {"permissions": {"deny": deny}})
    return ids, deny, selected, skipped, desired, replaces, env


SELFTESTS_TAIL = "Takes effect in new sessions."


def cmd_apply(args, paths):
    items = load_items()
    manifest = load_manifest(paths)
    force = set(split_csv(args.force_keys))
    target = paths.settings(args.scope)
    user_settings = safe_read(paths.settings("user"))[0]
    ids, deny, selected, skipped, desired, replaces, env = build_request(args, paths, user_settings, items)
    current, raw = read_json_file(target)
    ops = plan_settings(current, desired, force, replaces)
    changes = [op for op in ops if op["op"] in CHANGE_OPS]
    files, file_notes = plan_files(paths, selected, env, user_settings, force, manifest, False)
    file_changes = [f for f in files if f["state"] in ("create", "update", "replace")]
    if args.scope == "user" and (changes or file_changes):
        py_plans, _ = plan_files(paths, [], env, user_settings, force, manifest, True)
        files += py_plans
        file_changes += [f for f in py_plans if f["state"] == "create"]
    excludes = plan_exclude(paths, args.scope) if changes else []
    excludes = [e for e in excludes if e["line"] not in (read_text(e["path"]) or "").splitlines()]
    nothing = not changes and not file_changes and not excludes
    report = {"scope": args.scope, "file": target, "dry_run": args.dry_run, "items": ids, "skipped": skipped, "deny": deny,
              "ops": ops, "files": [{k: v for k, v in f.items() if k != "content"} for f in files], "notes": file_notes,
              "excludes": excludes, "backup": None, "changed": not nothing}
    head = "hetz apply%s — %s scope: %s" % (" (dry run)" if args.dry_run else "", args.scope, paths.tilde(target))
    lines = [head]
    if skipped:
        lines.append("  skipped (not applicable here): " + ", ".join(skipped))
    lines += describe_ops(ops)
    if files:
        lines.append("files:")
        lines += describe_files(files, paths)
    lines += ["  note: " + n for n in file_notes]
    lines += ["  + %s += %s" % (paths.tilde(e["path"]), e["line"]) for e in excludes]
    if nothing:
        lines.append("Nothing to change.")
        return finish(args, report, lines)
    bdir = backup_dir(paths, target, args.scope != "user")
    if args.dry_run:
        if changes and raw is not None:
            lines.append("backup on apply: %s" % paths.tilde(backup_path(target, "<ts>", bdir)))
        lines.append("Nothing written (dry run).")
        return finish(args, report, lines)

    ts = stamp()
    entry = {"id": "%s-%d" % (ts, os.getpid()), "ts": ts, "scope": args.scope, "file": target, "items": ids, "deny": deny,
             "created_file": False, "backup": None, "ops": [], "files": [], "excludes": []}
    try:
        for f in file_changes:
            entry["files"].append(write_file(f, ts))
        if changes:
            # Re-read right before writing: another tool (e.g. `rtk init`) may have edited the file meanwhile.
            current, raw = read_json_file(target)
            ops = plan_settings(current, desired, force, replaces)
            changes = [op for op in ops if op["op"] in CHANGE_OPS]
            report["ops"] = ops
            if changes:
                if raw is not None:
                    entry["backup"] = make_backup(target, backup_path(target, ts, bdir))
                else:
                    entry["created_file"] = True
                    entry["dirs"] = makedirs_recorded(os.path.dirname(target))
                write_atomic(target, dumps(apply_ops(current, changes)))
                entry["ops"] = changes
        for e in excludes:
            text = read_text(e["path"]) or ""
            os.makedirs(os.path.dirname(e["path"]), exist_ok=True)
            with open(e["path"], "a", encoding="utf-8") as fh:
                fh.write(("" if not text or text.endswith("\n") else "\n") + e["line"] + "\n")
            entry["excludes"].append(e)
    finally:
        if entry["ops"] or entry["files"] or entry["excludes"]:
            manifest["entries"].append(entry)
            save_manifest(paths, manifest)
    report["backup"] = entry["backup"]
    lines.append("Applied. Backup: %s · manifest: %s" % (paths.tilde(entry["backup"]) if entry["backup"] else "none (new file)",
                                                       paths.tilde(paths.manifest)))
    tests = [it["selftest"] for it in selected if it.get("selftest")]
    if tests:
        lines.append("Self-tests in a NEW session:")
        lines += ["  - " + t for t in tests]
    if deny:
        lines.append("Self-test: ask Claude to Read one denied file -> expect a denial.")
    if env["shell"] == "fish" and any(it["id"] == "launchers" for it in selected):
        lines.append("Launchers: cc, cc-deep, cc-quick, cc-spec <idea>, cc-do <spec> (new fish shells).")
    lines.append(SELFTESTS_TAIL)
    return finish(args, report, lines)


def write_file(plan, ts):
    dst = plan["dst"]
    record = {"path": dst, "sha256": sha(plan["content"])}
    if plan["state"] == "create":
        record["op"] = "file_create"
        record["dirs"] = makedirs_recorded(os.path.dirname(dst))
    else:
        record["op"] = "file_replace"
        record["backup"] = make_backup(dst, backup_path(dst, ts, plan.get("backup_dir")))
    write_atomic(dst, plan["content"], plan["mode"])
    return record


def finish(args, report, lines):
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(lines))
    return 0


# ---------------------------------------------------------------- undo

def undo_file(fop, force, dry):
    path = fop["path"]
    have = read_text(path) if os.path.lexists(path) else None
    matches = have is not None and sha(have) == fop["sha256"]
    if fop["op"] == "file_create":
        if have is None and not os.path.lexists(path):
            return ("=", path, "already gone")
        if not (matches or force):
            return ("!", path, "modified since apply; kept")
        if not dry:
            os.remove(path)
            for d in reversed(fop.get("dirs", [])):
                try:
                    os.rmdir(d)
                except OSError:
                    break
        return ("-", path, "removed")
    backup = fop.get("backup")
    if not backup or not os.path.exists(backup):
        return ("!", path, "backup missing; kept")
    if not (matches or force):
        return ("!", path, "modified since apply; kept (backup %s)" % backup)
    if not dry:
        content = read_text(backup)
        if content is None:
            os.replace(backup, path)
        else:  # write through a dotfile symlink instead of replacing the link itself
            write_atomic(path, content, stat.S_IMODE(os.stat(backup).st_mode))
            os.remove(backup)
    return ("<", path, "restored from backup")


def undo_exclude(e, dry):
    text = read_text(e["path"])
    if text is None:
        return None
    lines = text.splitlines()
    if e["line"] not in lines:
        return None
    if not dry:
        lines.remove(e["line"])
        write_atomic(e["path"], "\n".join(lines) + ("\n" if lines else ""))
    return ("-", e["path"], e["line"])


def cmd_undo(args, paths):
    manifest = load_manifest(paths)
    entries = manifest.get("entries", [])
    if args.scope == "all":
        targets = list(entries)
    else:
        f = paths.settings(args.scope)
        targets = [e for e in entries if e.get("file") == f]
    lines = ["hetz undo%s — scope %s" % (" (dry run)" if args.dry_run else "", args.scope)]
    report = {"scope": args.scope, "dry_run": args.dry_run, "entries": len(targets), "notes": []}
    if not targets:
        lines.append("Nothing to undo (no hetz changes recorded for this scope).")
        return finish(args, report, lines)
    docs, created, created_dirs, external = {}, {}, {}, {}
    notes = []
    ts = stamp()
    for entry in reversed(targets):
        path = entry["file"]
        if entry.get("ops"):
            if path not in docs:
                docs[path] = read_json_file(path)[0]
                external[path] = entry.get("scope") != "user"
            for mark, key, detail in undo_ops(docs[path], entry["ops"], args.force):
                notes.append((mark, "%s: %s" % (paths.tilde(path), key), detail))
            if entry.get("created_file"):
                created[path] = True
                created_dirs[path] = entry.get("dirs", [])
        for fop in reversed(entry.get("files", [])):
            mark, p, detail = undo_file(fop, args.force, args.dry_run)
            notes.append((mark, paths.tilde(p), detail))
        for e in entry.get("excludes", []):
            res = undo_exclude(e, args.dry_run)
            if res:
                notes.append((res[0], paths.tilde(res[1]), res[2]))
    for path, doc in docs.items():
        if args.dry_run:
            continue
        if created.get(path) and doc == {}:
            if os.path.exists(path):
                os.remove(os.path.realpath(path))
            for d in reversed(created_dirs.get(path, [])):
                try:
                    os.rmdir(d)
                except OSError:
                    break
            notes.append(("-", paths.tilde(path), "removed (hetz created it)"))
            continue
        current_raw = read_text(path)
        if current_raw is not None and dumps(doc) != current_raw:
            make_backup(path, backup_path(path, ts, backup_dir(paths, path, external.get(path, True))))
            write_atomic(path, dumps(doc))
    lines += ["  %s %s %s" % n for n in notes]
    if not args.dry_run:
        ids = {e["id"] for e in targets}
        manifest["entries"] = [e for e in entries if e["id"] not in ids]
        save_manifest(paths, manifest)
        lines.append("Undone. Takes effect in new sessions.")
    else:
        lines.append("Nothing written (dry run).")
    report["notes"] = [list(n) for n in notes]
    return finish(args, report, lines)


# ---------------------------------------------------------------- deny-scan (REPO mode)

GENERATED_DIRS = {"dist", ".next", ".nuxt", "coverage", ".turbo", ".parcel-cache", "storybook-static", ".svelte-kit",
                  ".output", ".docusaurus"}
# Often hand-written when tracked (e.g. build/webpack.config.js): recommended only when contents look generated.
BUILD_NAMED_DIRS = {"build", "out", "target", "generated", "__generated__"}
GEN_MARKERS = (b"@generated", b"do not edit", b"code generated", b"auto-generated", b"autogenerated",
               b"automatically generated")
BINARY_EXTS = {".class", ".o", ".obj", ".a", ".so", ".dylib", ".dll", ".exe", ".jar", ".war", ".pyc", ".rlib",
               ".rmeta", ".wasm", ".pdb"}
GEN_SAMPLE = 200  # files per build-named dir whose head is read
VENDOR_DIRS = {"vendor", "third_party"}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "uv.lock", "Gemfile.lock",
             "composer.lock", "go.sum", "bun.lockb", "bun.lock", "Pipfile.lock", "pdm.lock", "mix.lock", "Podfile.lock"}
DATA_EXTS = {".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".xml", ".sql", ".txt", ".yaml", ".yml", ".html"}
DATA_MIN_BYTES = 200 * 1024
SCAN_MIN_BYTES = 4096  # smaller groups are not worth a rule (or a question option)
WALK_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache"}


def list_repo_files(root, limit=200000):
    code, out = 1, ""
    try:
        proc = subprocess.run(["git", "-C", root, "ls-files", "-z", "-co", "--exclude-standard"],
                              capture_output=True, timeout=30)
        code, out = proc.returncode, proc.stdout.decode("utf-8", "replace")
    except Exception:
        pass
    if code == 0:
        return [p for p in out.split("\0") if p][:limit], True
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in WALK_SKIP]
        rel = os.path.relpath(dirpath, root)
        for name in filenames:
            files.append(name if rel == "." else os.path.join(rel, name).replace(os.sep, "/"))
            if len(files) >= limit:
                return files, False
    return files, False


def existing_deny(paths):
    rules = []
    for scope in SCOPES:
        data, _ = safe_read(paths.settings(scope))
        perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
        rules += [r for r in perms.get("deny", []) if isinstance(r, str)]
    return rules


def classify(rel, size):
    """(rule, kind, recommended) or None; recommended is None for build-named dirs (decided by looks_generated)."""
    parts = rel.split("/")
    for i, part in enumerate(parts[:-1]):
        if part in GENERATED_DIRS or part in VENDOR_DIRS or part in BUILD_NAMED_DIRS:
            rule = "Read(/%s/**)" % "/".join(parts[: i + 1])  # "/" = project root; "./x/**" denies x at any depth
            if part in BUILD_NAMED_DIRS:
                return rule, "build-named dir", None
            vendor = part in VENDOR_DIRS
            return rule, ("vendored" if vendor else "generated"), not vendor
    name = parts[-1]
    low = name.lower()
    m = re.search(r"\.min\.(js|mjs|cjs|css)$", low)
    if m:
        return "Read(**/*.min.%s)" % m.group(1), "minified", True
    m = re.search(r"\.(js|mjs|cjs|css)\.map$", low)
    if m:
        return "Read(**/*.%s.map)" % m.group(1), "source map", True
    if low.endswith(".snap"):
        return "Read(**/*.snap)", "snapshot", True
    if low.endswith(".log"):
        return "Read(**/*.log)", "log", True
    if name in LOCKFILES:
        return "Read(/%s)" % rel, "lockfile", False
    if os.path.splitext(low)[1] in DATA_EXTS and size > DATA_MIN_BYTES:
        return "Read(/%s)" % rel, "large data", True
    return None


def looks_generated(full, size):
    """Bundle/minified/map/binary output, or a generator header ('@generated', 'DO NOT EDIT') near the top."""
    low = os.path.basename(full).lower()
    ext = os.path.splitext(low)[1]
    if ext in BINARY_EXTS or low.endswith(".map") or re.search(r"\.min\.[a-z]+$", low) \
            or re.search(r"(\.bundle|\.chunk|[.-][0-9a-f]{8,})\.(m|c)?(js|css)$", low):
        return True
    try:
        with open(full, "rb") as f:
            head = f.read(2048)
    except OSError:
        return False
    if b"\0" in head or any(m in head.lower() for m in GEN_MARKERS):
        return True
    return ext in (".js", ".mjs", ".cjs", ".css") and size > len(head) and b"\n" not in head  # one-line = minified


def norm_rule(rule):
    return rule.replace("(./", "(")


def deny_scan(paths, limit=12):  # 12 = three 4-option questions next to the "Where?" question
    root = paths.project
    code, top = git(root, "rev-parse", "--show-toplevel")
    files, from_git = list_repo_files(root)
    have = {norm_rule(r) for r in existing_deny(paths)}
    groups = {}
    for rel in files:
        full = os.path.join(root, rel)
        try:
            st = os.lstat(full)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        hit = classify(rel, st.st_size)
        if not hit:
            continue
        rule, kind, rec = hit
        if norm_rule(rule) in have:
            continue
        g = groups.setdefault(rule, {"rule": rule, "kind": kind, "recommended": rec, "files": 0, "bytes": 0,
                                     "_seen": 0, "_seen_bytes": 0, "_gen_bytes": 0})
        g["files"] += 1
        g["bytes"] += st.st_size
        if rec is None and g["_seen"] < GEN_SAMPLE:
            g["_seen"] += 1
            g["_seen_bytes"] += st.st_size
            if looks_generated(full, st.st_size):
                g["_gen_bytes"] += st.st_size
    for g in groups.values():
        seen, gen = g.pop("_seen_bytes"), g.pop("_gen_bytes")
        g.pop("_seen")
        if g["recommended"] is None:
            g["recommended"] = gen * 2 > seen
            g["kind"] = "generated" if g["recommended"] else "build-named dir%s" % (" (not gitignored)" if from_git else "")
    found = sorted((g for g in groups.values() if g["bytes"] >= SCAN_MIN_BYTES), key=lambda g: (-g["bytes"], g["rule"]))
    return {"root": root, "git": from_git, "git_root": top.strip() if code == 0 else None, "existing_deny": len(have),
            "candidates": found[:limit], "more": max(0, len(found) - limit), "tiny": len(groups) - len(found)}


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def tokens_k(nbytes):
    k = nbytes / 4000.0
    return "<1" if k < 1 else "%d" % round(k)


def cmd_deny_scan(args, paths):
    res = deny_scan(paths)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    out = ["deny candidates in %s (%s; est tokens = bytes/4; Read deny also blocks Edit/Write):" % (
        paths.tilde(res["root"]), "gitignored files excluded" if res["git"] else "not a git repo")]
    for c in res["candidates"]:
        out.append("  [%s] %s — %s, %d file%s, %s (~%sk tok)" % (
            "x" if c["recommended"] else " ", c["rule"], c["kind"], c["files"], "" if c["files"] == 1 else "s",
            human_bytes(c["bytes"]), tokens_k(c["bytes"])))
    if not res["candidates"]:
        out.append("  none: nothing bulky outside .gitignore and existing deny rules")
    if res["more"] or res["tiny"]:
        out.append("  (+%d smaller candidates not shown)" % (res["more"] + res["tiny"]))
    out.append("existing Read/Edit deny rules: %d · [x] = recommended; write with: apply --scope local --deny '<rule>' ..." % res["existing_deny"])
    print("\n".join(out))
    return 0


# ---------------------------------------------------------------- cli

def build_parser():
    # Global options are accepted before or after the subcommand; SUPPRESS keeps a subparser from
    # resetting a value given before it (argparse would otherwise overwrite it with the default).
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--home", help="stand-in for ~ (default: $HETZ_HOME or the real home)")
    common.add_argument("--project", help="project dir for project/local scope (default: cwd)")
    common.add_argument("--shell", help="launcher shell (default: login shell)")
    common.add_argument("--auth", choices=["api", "subscription", "cloud"], help="override auth detection")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    p = argparse.ArgumentParser(prog="hetz_settings.py", description=__doc__.split("\n\n")[0], parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[common], help="report state, conflicts and what to ask")
    a = sub.add_parser("apply", parents=[common], help="merge profile items / deny rules into a settings file")
    a.add_argument("--scope", choices=SCOPES, default="user")
    a.add_argument("--items", action="append", help="comma-separated item ids (see status)")
    a.add_argument("--deny", action="append", help="permission rule to add to permissions.deny (repeatable)")
    a.add_argument("--force-keys", action="append", help="dotted keys / file:<name> allowed to replace existing values")
    a.add_argument("--dry-run", action="store_true")
    u = sub.add_parser("undo", parents=[common], help="restore exactly what hetz changed")
    u.add_argument("--scope", choices=SCOPES + ("all",), default="user")
    u.add_argument("--force", action="store_true", help="undo even values changed since apply")
    u.add_argument("--dry-run", action="store_true")
    sub.add_parser("deny-scan", parents=[common], help="per-repo Read deny candidates")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    for name, default in (("home", None), ("project", None), ("shell", None), ("auth", None), ("json", False)):
        if not hasattr(args, name):
            setattr(args, name, default)
    paths = Paths(args.home, args.project)
    try:
        if args.cmd == "status":
            st = gather_status(paths, args)
            if args.json:
                print(json.dumps(st, indent=2, ensure_ascii=False))
            else:
                print_status(st, paths)
            return 0
        if args.cmd == "apply":
            return cmd_apply(args, paths)
        if args.cmd == "undo":
            return cmd_undo(args, paths)
        if args.cmd == "deny-scan":
            return cmd_deny_scan(args, paths)
    except SetupError as e:
        print("hetz: " + str(e), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
