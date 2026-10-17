#!/usr/bin/env python3
"""/hetz:slim audit: always-loaded context sizes and Read-deny candidates for the current repo.

Prints <= 40 lines of names, sizes and estimates (tokens ~= chars/4). Never prints file contents.
  slim_audit.py [--deny-only] [--plugins | --plugins-only] [--json] [--cwd DIR]
  slim_audit.py --add-deny [--scope local|project] 'Read(/dist/**)' ...
  slim_audit.py --backup FILE ...   (untracked or uncommitted files -> FILE.hetz-bak[.N]; clean tracked = git)
Rules use a leading '/' (= the session cwd for local/project settings): Read(./dist/**) would deny
every dir named dist at any depth.

Load model (verified against Claude Code 2.1.272): managed CLAUDE.md + rules, ~/.claude/CLAUDE.md +
~/.claude/rules, then for every dir from / down to cwd: CLAUDE.md, .claude/CLAUDE.md,
.claude/rules/**/*.md without `paths:`, CLAUDE.local.md. @imports expand up to 4 hops (code spans
and fences skipped). Block <!-- --> comments and frontmatter are stripped before loading.
Auto memory loads the first 200 lines / 25,000 chars of MEMORY.md, or (flag tengu_stone_shell) an
index of memory/*.md plus pinned topic files. Python 3 stdlib only.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections import deque
from functools import lru_cache

MAX_OUT = 40                     # hard cap on printed lines
CPT = 4                          # chars per token (estimate)
WARN_LINES, OVER_LINES = 60, 200
MEM_LINES, MEM_CHARS = 200, 25000
IMPORT_DEPTH = 5                 # files at depth 0..4 load (4 hops)
READ_CAP = 4 << 20               # Claude skips memory files above 4 MiB
WALK_ENTRIES, WALK_SECONDS = 60000, 3.0
DIR_ENTRIES = 20000              # per generated-dir size walk (no-git mode)
FILE_CAP, STAT_CAP = 300000, 60000
GIT_TIMEOUT = 8
KB = 1024
DATA_MIN = 200 * KB
GROUP_MIN = {"build": 100 * KB, "gen": 100 * KB, "min": 20 * KB, "map": 20 * KB, "snap": 50 * KB,
             "logs": 20 * KB, "data": DATA_MIN, "lock": 20 * KB}
GROUPS = [  # key, label, suggested by default
    ("build", "build output dirs", True), ("min", "minified", True), ("map", "source maps", True),
    ("snap", "snapshots", True), ("data", "data/fixtures >200 KB", True), ("logs", "logs", True),
    ("gen", "vendored/generated code", False), ("lock", "lockfiles", False)]
GROUP_NOTE = {"gen": " (Claude may need to read it)", "lock": " (deny also blocks Edit)"}

BUILD_DIRS = {"dist", "build", "out", ".next", ".nuxt", "coverage", "target", ".svelte-kit", ".output",
              ".turbo", "htmlcov"}
VENDOR_DIRS = {"vendor", "third_party", "generated", "__generated__"}   # committed code: opt-in only
GEN_DIRS = BUILD_DIRS | VENDOR_DIRS
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", ".tox", ".nox", "__pycache__",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".idea", ".cache", ".terraform",
             ".direnv", "bower_components", "Pods", ".eggs", ".pnpm-store", ".yarn"}
LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock",
             "bun.lockb", "deno.lock", "Cargo.lock", "poetry.lock", "uv.lock", "Pipfile.lock",
             "pdm.lock", "Gemfile.lock", "composer.lock", "go.sum", "mix.lock", "pubspec.lock",
             "Podfile.lock", "Package.resolved", "flake.lock", "packages.lock.json", "gradle.lockfile"}
DATA_EXT = {".json", ".jsonl", ".ndjson", ".geojson", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".sql",
            ".txt", ".har", ".dat", ".parquet", ".avro", ".pb", ".bin", ".npy", ".pkl", ".db", ".sqlite"}
FIXTURE_DIRS = {"fixtures", "__fixtures__", "testdata", "test-data", "test_data", "datasets", "samples"}
SOURCE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb",
              ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".sh", ".md",
              ".toml", ".ini", ".cfg", ".lua", ".ex", ".exs", ".vue", ".svelte", ".css", ".scss", ".html"}
# @import targets Claude Code accepts (extension-less paths are accepted too).
TEXT_EXT = set((
    ".md .txt .text .json .yaml .yml .toml .xml .csv .html .htm .css .scss .sass .less .js .ts .tsx "
    ".jsx .mjs .cjs .mts .cts .py .pyi .pyw .rb .erb .rake .go .rs .java .kt .kts .scala .c .cpp .cc "
    ".cxx .h .hpp .hxx .cs .swift .sh .bash .zsh .fish .ps1 .bat .cmd .env .ini .cfg .conf .config "
    ".properties .sql .graphql .gql .proto .vue .svelte .astro .ejs .hbs .pug .jade .php .pl .pm .lua "
    ".r .dart .ex .exs .erl .hrl .clj .cljs .cljc .edn .hs .lhs .elm .ml .mli .f .f90 .f95 .for .cmake "
    ".make .makefile .gradle .sbt .rst .adoc .asciidoc .org .tex .latex .lock .log .diff .patch").split())
OFF = ("0", "false", "no", "off")
COMPACT_RE = re.compile(r"^ {0,3}#{1,6}\s*(compact|summary)\s+instructions\b", re.I | re.M)
IMPORT_RE = re.compile(r"(?:^|\s)@((?:[^\s\\]|\\ )+)")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(SKILL_DIR, "..", "..", "templates", "compact-instructions.md")
TEMPLATE_FALLBACK = ("## Compact instructions\n"
                     "- KEEP: goal, decisions + why, touched paths, failing tests with decisive lines, verify commands.\n"
                     "- DROP: exploration output, resolved errors, file contents, tool logs.\n")


# ---------- small helpers ----------

def est(chars):
    return (max(0, int(chars)) + CPT - 1) // CPT


def ktok(t):
    if t >= 10 ** 6:
        return "~{:,.1f}M".format(t / 1e6)
    return "~{:,.0f}k".format(t / 1000.0) if t >= 10000 else "~{:,}".format(t)


def human(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return ("{:.0f} {}" if unit == "B" else "{:.1f} {}").format(n, unit)
        n /= 1024.0
    return str(n)


def load_json(path):
    try:
        if os.path.getsize(path) > 32 << 20:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_text(path):
    try:
        if os.path.getsize(path) > READ_CAP:
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def merge(a, b):
    """b over a; lists concatenate (deduped), dicts merge recursively."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = list(dict.fromkeys(json.dumps(x, sort_keys=True) for x in out[k] + v))
            out[k] = [json.loads(x) for x in out[k]]
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def under(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def truthy_off(v):
    return v is not None and str(v).strip().lower() in OFF


def run(cmd, cwd, timeout=GIT_TIMEOUT):
    try:
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout, env=env)
        return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None
    except Exception:
        return None


@lru_cache(maxsize=4096)
def glob_rx(pat):
    """Glob -> regex: ** spans dirs, * and ? stay in a segment, [..] classes, {a,b} alternation."""
    out, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pat.startswith("/**", i) and i + 3 == n:
            out.append("(?:/.*)?"); i += 3
        elif pat.startswith("**", i):
            out.append(".*"); i += 2
        elif c == "*":
            out.append("[^/]*"); i += 1
        elif c == "?":
            out.append("[^/]"); i += 1
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c)); i += 1
            else:
                body = pat[i + 1:j].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]"); i = j + 1
        elif c == "{":
            j = pat.find("}", i + 1)
            if j == -1:
                out.append(re.escape(c)); i += 1
            else:
                out.append("(?:" + "|".join(glob_rx(x).pattern for x in pat[i + 1:j].split(",")) + ")")
                i = j + 1
        else:
            out.append(re.escape(c)); i += 1
    return re.compile("".join(out), re.S)


def gi_match(pat, rel):
    """gitignore-style test of a /-separated relative path (a matched directory covers its contents)."""
    pat = pat.strip()
    if not pat or pat.startswith("#") or pat.startswith("!"):
        return False
    anchored = pat.startswith("/") or "/" in pat.rstrip("/")
    core = pat.strip("/") or "**"
    parts = [p for p in rel.split("/") if p]
    if not any(c in core for c in "*?[{\\"):   # literal fast path
        if anchored:
            joined = "/".join(parts)
            return joined == core or joined.startswith(core + "/")
        return core in parts
    rx = glob_rx(core)
    if anchored:
        return any(rx.fullmatch("/".join(parts[:i])) for i in range(1, len(parts) + 1))
    return any(rx.fullmatch(p) for p in parts)


# ---------- context ----------

class Ctx:
    def __init__(self, cwd):
        self.cwd = os.path.realpath(cwd)
        self.home = os.path.realpath(os.path.expanduser("~"))
        cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        self.config_dir = os.path.realpath(os.path.expanduser(cfg)) if cfg else os.path.join(self.home, ".claude")
        self.global_config = os.path.join(self.config_dir if cfg else self.home, ".claude.json")
        self.managed_dir = ("/Library/Application Support/ClaudeCode" if sys.platform == "darwin"
                            else "/etc/claude-code")
        self.git = self._git()
        self.gcfg = load_json(self.global_config)
        self.gcfg = self.gcfg if isinstance(self.gcfg, dict) else {}
        self.settings = {
            "managed": load_json(os.path.join(self.managed_dir, "managed-settings.json")) or {},
            "user": load_json(os.path.join(self.config_dir, "settings.json")) or {},
            "project": load_json(os.path.join(self.cwd, ".claude", "settings.json")) or {},
            "local": load_json(os.path.join(self.cwd, ".claude", "settings.local.json")) or {},
        }
        for k, v in list(self.settings.items()):
            if not isinstance(v, dict):
                self.settings[k] = {}
        main = (self.git or {}).get("main")
        if main and main != self.cwd:   # 2.1.272 keeps local settings at the main repo root; cwd's file still merges
            canon = load_json(os.path.join(main, ".claude", "settings.local.json"))
            if isinstance(canon, dict):
                self.settings["local"] = merge(canon, self.settings["local"])

    def _git(self):
        if not shutil.which("git"):
            return None
        out = run(["git", "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"], self.cwd)
        if not out:
            return None
        lines = out.splitlines()
        top = os.path.realpath(lines[0])
        common = lines[1] if len(lines) > 1 else ""
        main = os.path.dirname(os.path.realpath(common)) if os.path.basename(common) == ".git" else top
        return {"top": top, "main": main}

    def setting(self, key, order=("managed", "local", "project", "user")):
        for src in order:
            v = self.settings[src].get(key)
            if v is not None:
                return v
        return None

    def env_value(self, key):
        if key in os.environ:
            return os.environ[key]
        for src in ("managed", "local", "project", "user"):
            env = self.settings[src].get("env")
            if isinstance(env, dict) and key in env:
                return env[key]
        return None

    def disp(self, path):
        if under(path, self.cwd):
            return os.path.relpath(path, self.cwd)
        if under(path, self.home):
            return "~/" + os.path.relpath(path, self.home)
        return path


# ---------- memory files ----------

def split_frontmatter(text):
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def split_globs(val):
    items, cur, depth = [], "", 0
    for ch in val:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(cur); cur = ""
        else:
            cur += ch
    items.append(cur)
    return [x.strip().strip("'\"").strip() for x in items if x.strip().strip("'\"").strip()]


def fm_paths(fm):
    """`paths:` globs from rule frontmatter; [] means the rule is unconditional (always loaded)."""
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^paths\s*:\s*(.*)$", line)
        if not m:
            continue
        val, items = m.group(1).strip(), []
        if val:
            if val.startswith("[") and val.endswith("]"):
                val = val[1:-1]
            items = split_globs(val)
        else:
            for nxt in lines[i + 1:]:
                li = re.match(r"^\s*-\s*(.*)$", nxt)
                if li:
                    items.extend(split_globs(li.group(1)))
                elif nxt.strip():
                    break
        items = [x[:-3] if x.endswith("/**") else x for x in items]
        items = [x for x in items if x]
        return [] if not items or all(x == "**" for x in items) else items
    return []


def strip_comments(text):
    """Drop block-level <!-- --> comments (as Claude Code does); returns (kept_text, removed_chars)."""
    lines = text.splitlines(True)
    out, removed, i, fence = [], 0, 0, None
    while i < len(lines):
        line = lines[i]
        fm = FENCE_RE.match(line)
        if fence:
            out.append(line); i += 1
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= len(fence):
                fence = None
            continue
        if fm:
            fence = fm.group(1); out.append(line); i += 1
            continue
        s = line.lstrip(" ")
        if len(line) - len(s) <= 3 and s.startswith("<!--"):
            j, raw = i, ""
            while j < len(lines):
                raw += lines[j]
                if "-->" in raw[raw.index("<!--") + 4:]:
                    break
                j += 1
            if "-->" in raw[raw.index("<!--") + 4:]:
                kept = re.sub(r"<!--[\s\S]*?-->", "", raw)
                kept = kept if kept.strip() else ""
                removed += len(raw) - len(kept)
                out.append(kept); i = j + 1
                continue
        out.append(line); i += 1
    return "".join(out), removed


def find_imports(text, base_dir, home):
    found, fence = [], None
    for line in text.splitlines():
        fm = FENCE_RE.match(line)
        if fence:
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= len(fence):
                fence = None
            continue
        if fm:
            fence = fm.group(1)
            continue
        line = re.sub(r"(`+)[^`]*?\1", " ", line)
        for m in IMPORT_RE.finditer(line):
            p = m.group(1)
            if "#" in p:
                p = p[:p.index("#")]
            p = p.replace("\\ ", " ")
            if not p:
                continue
            ok = (p.startswith("./") or p.startswith("~/") or (p.startswith("/") and p != "/")
                  or (not p.startswith("@") and not re.match(r"^[#%^&*()]+", p)
                      and re.match(r"^[a-zA-Z0-9._-]", p)))
            if not ok:
                continue
            if p.startswith("~/"):
                path = os.path.join(home, p[2:])
            elif os.path.isabs(p):
                path = p
            else:
                path = os.path.join(base_dir, p)
            found.append(os.path.normpath(path))
    return found


def mem_truncate(text):
    """MEMORY.md load cap: first 200 lines, then 25,000 chars cut at a line end."""
    t = text.strip()
    lines = t.count("\n") + 1
    if lines <= MEM_LINES and len(t) <= MEM_CHARS:
        return t, 0
    s = "\n".join(t.split("\n")[:MEM_LINES]) if lines > MEM_LINES else t
    if len(s) > MEM_CHARS:
        k = s.rfind("\n", 0, MEM_CHARS)
        s = s[:k if k > 0 else MEM_CHARS]
    kept = s.count("\n") + 1 if s else 0
    return s, max(0, lines - kept)


def rules_files(rules_dir, cap=500, depth=0):
    """*.md under a rules dir, recursive, entries in name order (dirs and files interleaved)."""
    out = []
    try:
        entries = sorted(os.scandir(rules_dir), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if len(out) >= cap:
            break
        if e.is_dir() and depth < 8:
            out += rules_files(e.path, cap - len(out), depth + 1)
        elif e.is_file() and e.name.endswith(".md"):
            out.append(e.path)
    return out


def collect_memory(ctx):
    rows, on_demand, seen = [], [], set()
    stats = {"missing": 0, "unapproved": 0, "excluded": 0, "stripped": 0, "compact": False}
    excludes = []
    for src in ("managed", "user", "project", "local"):
        v = ctx.settings[src].get("claudeMdExcludes")
        if isinstance(v, list):
            excludes += [glob_rx(str(x).replace("\\", "/")) for x in v if str(x).strip()]
    proj_cfg = ctx.gcfg.get("projects") if isinstance(ctx.gcfg.get("projects"), dict) else {}
    approved = any(isinstance(proj_cfg.get(k), dict) and proj_cfg[k].get("hasClaudeMdExternalIncludesApproved")
                   for k in (ctx.cwd, (ctx.git or {}).get("top")) if k)

    def add(path, kind, origin, depth=0, parent=None):
        real = os.path.realpath(path)
        if real in seen or depth >= IMPORT_DEPTH or not os.path.isfile(real):
            return
        if origin != "managed" and any(rx.fullmatch(path.replace("\\", "/")) for rx in excludes):
            stats["excluded"] += 1
            return
        text = read_text(real)
        seen.add(real)
        if text is None or not text.strip():
            return
        fm, body = split_frontmatter(text)
        if kind == "rule" and fm_paths(fm):
            on_demand.append(("rule", real, est(len(body))))
            return
        kept, removed = strip_comments(body)
        stats["stripped"] += removed
        cut = 0
        if kind in ("memory", "pinned"):
            kept, cut = mem_truncate(kept)
        if COMPACT_RE.search(kept):
            stats["compact"] = True
        rows.append({"kind": kind, "path": real, "lines": len(text.splitlines()),
                     "tokens": est(len(kept.strip())), "parent": parent, "cut_lines": cut})
        if kind in ("memory", "pinned"):   # auto-memory files never expand @imports
            return
        for imp in find_imports(kept, os.path.dirname(path), ctx.home):
            ext = os.path.splitext(imp)[1].lower()
            if ext and ext not in TEXT_EXT:
                continue
            if not os.path.isfile(imp):
                stats["missing"] += 1
                continue
            if origin in ("project", "local") and not approved and not under(os.path.realpath(imp), ctx.cwd):
                stats["unapproved"] += 1
                continue
            add(imp, "import", origin, depth + 1, real)

    md = ctx.managed_dir
    add(os.path.join(md, "CLAUDE.md"), "managed", "managed")
    for f in rules_files(os.path.join(md, ".claude", "rules")):
        add(f, "rule", "managed")
    add(os.path.join(ctx.config_dir, "CLAUDE.md"), "user", "user")
    for f in rules_files(os.path.join(ctx.config_dir, "rules")):
        add(f, "rule", "user")
    chain, d = [], ctx.cwd
    while d != os.path.dirname(d):
        chain.append(d)
        d = os.path.dirname(d)
    for d in reversed(chain):
        add(os.path.join(d, "CLAUDE.md"), "project", "project")
        add(os.path.join(d, ".claude", "CLAUDE.md"), "project", "project")
        for f in rules_files(os.path.join(d, ".claude", "rules")):
            add(f, "rule", "project")
        add(os.path.join(d, "CLAUDE.local.md"), "local", "local")
    mem = auto_memory(ctx)
    for p in mem.get("pinned", []):   # index mode only; injected before the index
        add(p, "pinned", "memory")
    ix = mem.get("index")
    if ix:
        rows.append({"kind": "index", "path": mem["dir"], "lines": ix["files"], "tokens": est(ix["chars"]),
                     "parent": None, "cut_lines": ix["cut"]})
    elif mem.get("path"):
        add(mem["path"], "memory", "memory")
    return rows, on_demand, stats, mem


def auto_memory(ctx):
    if str(os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "")).strip().lower() in ("1", "true", "yes", "on"):
        return {"state": "off (CLAUDE_CODE_DISABLE_AUTO_MEMORY)"}
    if ctx.setting("autoMemoryEnabled") is False:
        return {"state": "off (autoMemoryEnabled: false)"}
    override = ctx.setting("autoMemoryDirectory", ("managed", "local", "user"))
    if isinstance(override, str) and override.strip():
        mdir = os.path.expanduser(override.strip())
    else:
        key = (ctx.git or {}).get("main") or ctx.cwd
        name = re.sub(r"[^a-zA-Z0-9]", "-", key)
        base = os.path.join(os.environ.get("CLAUDE_CODE_REMOTE_MEMORY_DIR") or ctx.config_dir, "projects")
        mdir = os.path.join(base, name, "memory")
        if len(name) > 200:
            try:
                hits = sorted(x for x in os.listdir(base) if x.startswith(name[:200] + "-"))
                mdir = os.path.join(base, hits[0], "memory") if hits else mdir
            except OSError:
                pass
    path = os.path.join(mdir, "MEMORY.md")
    feats = ctx.gcfg.get("cachedGrowthBookFeatures")
    index_mode = (isinstance(feats, dict) and feats.get("tengu_stone_shell") is True
                  and not os.environ.get("CLAUDE_CODE_REMOTE")
                  and not os.environ.get("CLAUDE_COWORK_MEMORY_GUIDELINES", "").strip())
    if not index_mode:   # MEMORY.md only; pinned topic files load only in index mode
        return {"state": "on", "mode": "MEMORY.md", "path": path if os.path.isfile(path) else None,
                "dir": mdir, "pinned": []}
    pinned = []
    try:
        cands = []
        for f in sorted(os.listdir(mdir)):
            p = os.path.join(mdir, f)
            if f.endswith(".md") and f != "MEMORY.md" and os.path.isfile(p):
                head = (read_text(p) or "")[:2000]
                fm, _ = split_frontmatter(head)
                if re.search(r"^\s*pinned\s*:\s*true\s*$", fm, re.M | re.I):
                    cands.append((-os.path.getmtime(p), p))
        pinned = [p for _, p in sorted(cands)[:4]]
    except OSError:
        pass
    return {"state": "on", "mode": "index", "path": None, "dir": mdir, "pinned": pinned,
            "index": memory_index(mdir)}


def memory_index(mdir):
    """Size of the generated memory index (index mode): '- [name](path): description' per topic file,
    newest 200, capped at 25,000 chars. None when there are no topic files."""
    found = []

    def walk(d, rel, depth):
        if depth >= 8 or len(found) > 5000:
            return
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            r = rel + "/" + e.name if rel else e.name
            low = e.name.rstrip(". ").lower()
            try:
                if e.is_dir(follow_symlinks=False):
                    if not e.name.startswith(".") and low not in ("team", "logs", "sessions", "proposals"):
                        walk(e.path, r, depth + 1)
                elif (e.is_file(follow_symlinks=False) and low.endswith(".md") and not e.name.startswith(".")
                      and low != "memory.md" and e.stat().st_size <= 1 << 20):
                    found.append((-e.stat().st_mtime, r, e.path))
            except OSError:
                continue

    walk(mdir, "", 0)
    if not found:
        return None
    lines = []
    for _, rel, full in sorted(found)[:MEM_LINES]:
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                head = "".join(f.readline() for _ in range(30))
        except OSError:
            continue
        fm, body = split_frontmatter(head)
        meta = dict((k, v.strip().strip("'\"")) for k, v in re.findall(r"^(name|description)\s*:\s*(.*)$", fm, re.M))
        name = (meta.get("name") or re.sub(r"(?i)\.md$", "", os.path.basename(rel)))[:60]
        desc = meta.get("description")
        if not desc:
            first = next((l.lstrip("#").strip() for l in body.splitlines() if l.strip()), None)
            desc = None if first in (None, "---") else first
        desc = re.sub(r"\s+", " ", desc).strip()[:200] if desc else ""
        lines.append("- [{}]({}){}".format(name, rel, ": " + desc if desc else ""))
    cut = max(0, len(found) - len(lines))
    if len(found) > MEM_LINES:
        lines.append("... {} more memories not listed".format(cut))
    text = "\n".join(lines)
    if len(text) > MEM_CHARS:
        k = text.rfind("\n", 0, MEM_CHARS)
        text = text[:k if k > 0 else MEM_CHARS] + "\n... (truncated)"
    return {"files": len(found), "chars": len(text) + 70 + len(mdir), "cut": cut}


# ---------- deny rules ----------

def deny_rules(ctx):
    out = {}
    for src in ("managed", "user", "project", "local"):
        perms = ctx.settings[src].get("permissions")
        deny = perms.get("deny") if isinstance(perms, dict) else None
        out[src] = [r for r in deny if isinstance(r, str) and re.match(r"^Read\(.+\)$", r.strip())] \
            if isinstance(deny, list) else []
    return out


def rule_matchers(ctx, rules):
    """(root, pattern) pairs mirroring how Claude Code anchors Read() rule paths."""
    out = []
    for src, lst in rules.items():
        for r in lst:
            p = r.strip()[5:-1].strip()
            if p.startswith("//"):
                root, pat = "/", p[1:]
            elif p.startswith("~/"):
                root, pat = ctx.home, p[1:]
            elif p.startswith("/"):
                root, pat = (ctx.config_dir if src == "user" else ctx.cwd), p
            else:
                root, pat = ctx.cwd, p[2:] if p.startswith("./") else p
            if pat.endswith("/**") and re.search(r"[^/]", pat[:-3]):
                pat = pat[:-3]   # deny of dir/** matches the dir itself (any depth when unanchored)
            out.append((root, pat))
    return out


def is_denied(matchers, path):
    for root, pat in matchers:
        if under(path, root):
            rel = path[len(root):].lstrip("/") if root != "/" else path.lstrip("/")
            if rel and gi_match(pat, rel):
                return True
    return False


def claudeignore(ctx):
    for d in [ctx.cwd] + ([ctx.git["top"]] if ctx.git and ctx.git["top"] != ctx.cwd else []):
        p = os.path.join(d, ".claudeignore")
        if os.path.isfile(p):
            entries = [l.strip() for l in (read_text(p) or "").splitlines()
                       if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("!")]
            rules = []
            for e in entries:
                is_dir = e.endswith("/")
                core = e.strip("/")
                if not core:
                    continue
                anchored = e.startswith("/") or "/" in core
                base = "/" + core if anchored else "./**/" + core
                rules.append("Read({}{})".format(base, "/**" if is_dir else ""))
            return {"path": p, "entries": len(entries), "rules": rules}
    return None


# ---------- file listing + candidates ----------

def list_files(ctx):
    """Relative file paths under cwd: git ls-files (honors .gitignore) or a capped BFS walk."""
    if ctx.git:
        out = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], ctx.cwd)
        if out is not None:
            files = list(dict.fromkeys(f for f in out.split("\0") if f))
            capped = len(files) > FILE_CAP
            return files[:FILE_CAP], "git", capped
    files, n, capped, t0 = [], 0, False, time.monotonic()
    ign = []
    gi = os.path.join(ctx.cwd, ".gitignore")
    if os.path.isfile(gi):
        ign = [l.strip() for l in (read_text(gi) or "").splitlines() if l.strip() and not l.startswith("#")]
    queue = deque([""])
    while queue and not capped:
        rel = queue.popleft()
        try:
            with os.scandir(os.path.join(ctx.cwd, rel) if rel else ctx.cwd) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            n += 1
            if n > WALK_ENTRIES or time.monotonic() - t0 > WALK_SECONDS:
                capped = True
                break
            r = rel + "/" + e.name if rel else e.name
            if ign and any(gi_match(p, r) for p in ign):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name in SKIP_DIRS:
                        continue
                    if e.name in GEN_DIRS:
                        files.append(r + "/")   # sized later, never descended here
                        continue
                    queue.append(r)
                elif e.is_file(follow_symlinks=False):
                    files.append(r)
            except OSError:
                continue
    return files, "walk", capped


def dir_size(path, deadline):
    total = count = 0
    stack, capped = [path], False
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    count += 1
                    if count > DIR_ENTRIES or time.monotonic() > deadline:
                        return total, count, True
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total, count, capped


def classify(parts):
    name = parts[-1]
    low = name.lower()
    ext = os.path.splitext(low)[1]
    dirs = [p.lower() for p in parts[:-1]]
    if name in LOCKFILES:
        return "lock"
    if ".min." in low:
        return "min"
    if ext == ".map":
        return "map"
    if ext == ".snap":
        return "snap"
    if ext == ".log" or re.search(r"\.log\.\d+$", low) or (
            any(d in ("logs", "log") for d in dirs) and ext not in SOURCE_EXT):
        return "logs"
    if ext in DATA_EXT or (any(d in FIXTURE_DIRS for d in dirs) and ext not in SOURCE_EXT):
        return "data"
    return None


def candidates(ctx, files, mode, matchers):
    groups = {k: {} for k, _, _ in GROUPS}
    sub_md, src_dirs = [], set()
    stats = [0]
    deadline = time.monotonic() + WALK_SECONDS

    def size(rel):
        if stats[0] >= STAT_CAP:
            return None
        stats[0] += 1
        try:
            st = os.lstat(os.path.join(ctx.cwd, rel))
        except OSError:
            return None
        return None if stat.S_ISLNK(st.st_mode) else st.st_size

    for rel in files:
        if rel.endswith("/"):   # generated dir found by the walk
            key = rel.rstrip("/")
            b, n, capped = dir_size(os.path.join(ctx.cwd, key), deadline)
            groups["gen" if os.path.basename(key) in VENDOR_DIRS else "build"][key] = [b, n, capped]
            continue
        parts = rel.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        if parts[-1] in ("CLAUDE.md", "CLAUDE.local.md") and len(parts) > 1:
            sub_md.append(rel)
        gen = next((i for i, p in enumerate(parts[:-1]) if p in GEN_DIRS), None)
        if gen is not None:
            key = "/".join(parts[:gen + 1])
            b = size(rel)
            slot = groups["gen" if parts[gen] in VENDOR_DIRS else "build"].setdefault(key, [0, 0, False])
            slot[0] += b or 0
            slot[1] += 1
            slot[2] = slot[2] or b is None
            continue
        g = classify(parts)
        if not g:
            if any(p.lower() in ("logs", "log") for p in parts[:-1]) and \
                    os.path.splitext(parts[-1])[1].lower() in SOURCE_EXT:   # keeps logger.ts out of a logs/ deny
                src_dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
            continue
        b = size(rel)
        if b is None or (g == "data" and b <= DATA_MIN):
            continue
        groups[g][rel] = [b, 1, False]
    # drop already-denied items, then apply per-group thresholds
    for g, items in groups.items():
        for key in list(items):
            if is_denied(matchers, os.path.join(ctx.cwd, key)):
                del items[key]
        if g in ("build", "gen", "data", "lock"):
            for key in list(items):
                if items[key][0] < GROUP_MIN[g]:
                    del items[key]
        elif sum(v[0] for v in items.values()) < GROUP_MIN[g]:
            items.clear()
    return groups, sub_md, stats[0] >= STAT_CAP, src_dirs


def group_rules(g, items, src_dirs=()):
    """Path rules start with '/' (anchored to cwd); a './x/**' deny would match x at any depth."""
    keys = sorted(items, key=lambda k: (-items[k][0], k))
    if g in ("build", "gen"):
        return ["Read(/{}/**)".format(k) for k in keys]
    if g in ("min", "map", "snap", "logs") and len(keys) >= 3:
        if g == "min":
            exts = sorted({os.path.splitext(k)[1].lower() for k in keys})
            return ["Read(./**/*.min{})".format(e) for e in exts]
        if g == "logs":   # non-.log files: deny the outermost logs/ dir unless it also holds source
            logdirs, single = set(), []
            for k in keys:
                parts = k.split("/")
                i = next((i for i, p in enumerate(parts[:-1]) if p.lower() in ("logs", "log")), None)
                if i is None or k.endswith(".log"):
                    continue
                d = "/".join(parts[:i + 1])
                if d in src_dirs:
                    single.append(k)
                else:
                    logdirs.add(d)
            return (["Read(./**/*.log)"] if any(k.endswith(".log") for k in keys) else []) + \
                ["Read(/{}/**)".format(d) for d in sorted(logdirs)] + ["Read(/{})".format(k) for k in single]
        return ["Read(./**/*.{})".format(g)]
    return ["Read(/{})".format(k) for k in keys]


# ---------- glob / mcp / plugins ----------

def glob_status(ctx):
    v = ctx.env_value("CLAUDE_CODE_GLOB_NO_IGNORE")
    if truthy_off(v):
        return {"ok": True, "value": v, "ignored": []}
    ignored = []
    if ctx.git:
        out = run(["git", "ls-files", "-z", "-o", "-i", "--exclude-standard", "--directory"], ctx.cwd)
        ignored = sorted(x for x in (out or "").split("\0") if x)
    else:   # no git: Glob (rg --no-ignore --hidden) also walks dependency/cache dirs
        ignored = sorted(d + "/" for d in SKIP_DIRS if os.path.isdir(os.path.join(ctx.cwd, d)))
    return {"ok": False, "value": v, "ignored": ignored}


def mcp_status(ctx):
    cfg = ctx.gcfg
    servers = {}

    def take(src, d):
        if isinstance(d, dict):
            for name, conf in d.items():
                servers[name] = {"src": src, "alwaysLoad": isinstance(conf, dict) and conf.get("alwaysLoad") is True}

    take("user", cfg.get("mcpServers"))
    projects = cfg.get("projects") if isinstance(cfg.get("projects"), dict) else {}
    for k in (ctx.cwd, (ctx.git or {}).get("top")):
        if k and isinstance(projects.get(k), dict):
            take("local", projects[k].get("mcpServers"))
    take(".mcp.json", (load_json(os.path.join(ctx.cwd, ".mcp.json")) or {}).get("mcpServers"))
    upfront = None
    ts = str(os.environ.get("ENABLE_TOOL_SEARCH", "")).strip().lower()
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    if ts in OFF:
        upfront = "ENABLE_TOOL_SEARCH={} loads every MCP schema upfront".format(ts)
    elif base and "anthropic.com" not in base and ts not in ("1", "true", "yes", "on") and not ts.startswith("auto"):
        upfront = "non-Anthropic ANTHROPIC_BASE_URL loads MCP schemas upfront unless ENABLE_TOOL_SEARCH=true"
    return {"servers": servers, "upfront": upfront}


def plugin_status(ctx):
    enabled = {}
    for src in ("user", "project", "local"):
        ep = ctx.settings[src].get("enabledPlugins")
        if isinstance(ep, dict):
            enabled.update({k: v for k, v in ep.items() if isinstance(k, str)})
    names = sorted(k for k, v in enabled.items() if v is True)
    claude = shutil.which("claude")
    res = {"cli": bool(claude), "plugins": [], "overlaps": []}
    for name in names[:10]:
        tok = None
        if claude:
            out = run([claude, "plugin", "details", name], ctx.cwd, timeout=15)
            m = re.search(r"Always-on:\s*~?([\d,.]+)\s*(k?)\s*tok", out or "")
            if m:
                tok = int(float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1))
        res["plugins"].append({"name": name, "always_on": tok})
    style = str(ctx.setting("outputStyle") or "")
    if any(n.split("@")[0].startswith("caveman") for n in names) and style.lower() == "concise":
        res["overlaps"].append("caveman + Concise style: both trim prose; keep one")
    hooks_txt = json.dumps([ctx.settings[s].get("hooks") for s in ("user", "project", "local")])
    if re.search(r"\brtk\b", hooks_txt) and any(n.startswith("hetz@") for n in names):
        res["overlaps"].append("RTK hook + hetz squeeze: never filter the same command twice")
    if len(names) > 10:
        res["overlaps"].append("{} more enabled plugins not measured".format(len(names) - 10))
    return res


# ---------- report ----------

def audit(ctx, deny_only=False, plugins=False):
    rules = deny_rules(ctx)
    matchers = rule_matchers(ctx, rules)
    files, mode, capped = list_files(ctx)
    groups, sub_md, stat_capped, src_dirs = candidates(ctx, files, mode, matchers)
    data = {"cwd": ctx.cwd, "git": bool(ctx.git), "listing": mode, "walk_capped": capped or stat_capped,
            "deny_rules": rules, "claudeignore": claudeignore(ctx), "glob": glob_status(ctx), "candidates": []}
    for g, label, checked in GROUPS:
        items = groups[g]
        if items:
            b = sum(v[0] for v in items.values())
            data["candidates"].append({
                "group": g, "label": label, "suggested": checked, "bytes": b, "tokens": est(b),
                "paths": sorted(items, key=lambda k: (-items[k][0], k)),
                "capped": any(v[2] for v in items.values()), "rules": group_rules(g, items, src_dirs)})
    if not deny_only:
        rows, on_demand, mstats, mem = collect_memory(ctx)
        loaded = {r["path"] for r in rows}
        for rel in sub_md:
            p = os.path.join(ctx.cwd, rel)
            if os.path.realpath(p) not in loaded:
                try:
                    on_demand.append(("claude_md", p, est(os.path.getsize(p))))
                except OSError:
                    pass
        data.update({"memory": rows, "on_demand": [{"kind": k, "path": p, "tokens": t} for k, p, t in on_demand],
                     "memory_stats": mstats, "auto_memory": {k: v for k, v in mem.items() if k != "pinned"},
                     "mcp": mcp_status(ctx)})
    if plugins:
        data["plugins"] = plugin_status(ctx)
    return data


def render(ctx, data, deny_only=False, ignored_args=()):
    out = []
    where = "~" + ctx.cwd[len(ctx.home):] if under(ctx.cwd, ctx.home) else ctx.cwd
    head = "hetz slim audit · {} · {} · est. tok = chars/4".format(
        where, "git" if data["git"] else "no git (gitignore: root .gitignore only)")
    out.append(head + (" · deny-only" if deny_only else ""))
    if ignored_args:
        out.append("ignored args: " + " ".join(ignored_args)[:80])
    if not deny_only:
        rows = data["memory"]
        total = sum(r["tokens"] for r in rows)
        out.append("ALWAYS LOADED ~{:,} tok on every request (also in general-purpose/custom subagents)".format(total))
        if not rows:
            out.append("  (no CLAUDE.md, rules or MEMORY.md found)")
        label = {"managed": "managed", "user": "user", "project": "proj", "rule": "rule", "local": "local",
                 "import": "import", "memory": "memory", "pinned": "pinned", "index": "memory"}
        shown = rows[:12]
        for r in shown:
            name = ctx.disp(r["path"])
            if r["kind"] == "import":
                name = "@" + name + " <- " + os.path.basename(r["parent"] or "")
            if r["kind"] == "index":
                name += "/"
            if len(name) > 44 and r["kind"] not in ("memory", "pinned", "index"):   # memory path stays whole
                name = "…" + name[-43:]
            flags = []
            if r["kind"] in ("project", "local", "user", "import", "rule"):
                if r["lines"] > OVER_LINES:
                    flags.append("RED >200 ln")
                elif r["lines"] > WARN_LINES:
                    flags.append("yellow >60 ln")
            if r["kind"] == "rule":
                flags.append("no paths:")
            if r["kind"] == "memory":
                flags.append("auto memory; loads first 200 ln/25 KB" + (
                    ", {} ln cut".format(r["cut_lines"]) if r["cut_lines"] else ""))
            if r["kind"] == "index":
                flags.append("auto memory INDEX of {} topic files (MEMORY.md not loaded){}".format(
                    r["lines"], ", {} not listed".format(r["cut_lines"]) if r["cut_lines"] else ""))
            if r["kind"] == "pinned":
                flags.append("pinned topic file")
            out.append("  {:<6} {:<44} {:>5} ln {:>7} tok  {}".format(
                label[r["kind"]], name, r["lines"], "{:,}".format(r["tokens"]), "; ".join(flags)).rstrip())
        if len(rows) > len(shown):
            rest = rows[len(shown):]
            out.append("  +{} more files ~{:,} tok".format(len(rest), sum(r["tokens"] for r in rest)))
        ms = data["memory_stats"]
        extra = []
        if ms["stripped"]:
            extra.append("<!-- --> comments stripped: ~{:,} tok (free)".format(est(ms["stripped"])))
        if ms["missing"]:
            extra.append("{} @imports not found".format(ms["missing"]))
        if ms["unapproved"]:
            extra.append("{} @imports outside repo skipped (unapproved)".format(ms["unapproved"]))
        if ms["excluded"]:
            extra.append("{} files skipped by claudeMdExcludes".format(ms["excluded"]))
        am = data["auto_memory"]
        if am.get("state") != "on":
            extra.append("auto memory " + am.get("state", "off"))
        if extra:
            out.append("  " + " · ".join(extra))
        od = data["on_demand"]
        if od:
            nmd = [x for x in od if x["kind"] == "claude_md"]
            nru = [x for x in od if x["kind"] == "rule"]
            out.append("ON DEMAND: {} subdir CLAUDE.md ~{:,} tok · {} path-scoped rules ~{:,} tok".format(
                len(nmd), sum(x["tokens"] for x in nmd), len(nru), sum(x["tokens"] for x in nru)))
        if ms["compact"]:
            out.append("COMPACT: ok (a Compact instructions section is loaded)")
        else:
            out.append("COMPACT: missing; append to root CLAUDE.md:")
            tpl = read_text(TEMPLATE) or TEMPLATE_FALLBACK
            out.extend("  | " + l for l in tpl.strip().splitlines()[:4])
    ci = data["claudeignore"]
    if ci:
        out.append(".claudeignore: NO EFFECT ({} entries) -> migrate to Read deny: {}{}".format(
            ci["entries"], " ".join(ci["rules"][:4]), " +{} more".format(len(ci["rules"]) - 4) if len(ci["rules"]) > 4 else ""))
    rules = data["deny_rules"]
    allr = list(dict.fromkeys(r for src in ("managed", "user", "project", "local") for r in rules[src]))
    out.append("READ DENY in effect ({}): ".format(len(allr)) + " · ".join(
        "{} {}".format(s, len(rules[s])) for s in ("user", "project", "local", "managed") if rules[s] or s != "managed"))
    if allr:
        line = "  " + " ".join(allr)
        out.append(line if len(line) <= 150 else line[:146] + " …")
    cands = data["candidates"]
    out.append("DENY CANDIDATES ({}not denied; [x] = suggested){}:".format(
        "not gitignored, " if data["git"] else "", " walk capped" if data["walk_capped"] else ""))
    if not cands:
        out.append("  none above thresholds")
    for c in cands:
        rl, shown = c["rules"], []
        for r in c["rules"][:4]:   # keep each line short: whole rules only, ~200 chars
            if shown and len(" ".join(shown + [r])) > 150:
                break
            shown.append(r)
        out.append("  [{}] {:<5} {:>3} {} {:>9} {:>7} tok  {}{}{}".format(
            "x" if c["suggested"] else " ", c["group"], len(c["paths"]),
            "item " if len(c["paths"]) == 1 else "items", ("≥" if c["capped"] else "") + human(c["bytes"]),
            ktok(c["tokens"]), " ".join(shown), " +{} more".format(len(rl) - len(shown)) if len(rl) > len(shown) else "",
            GROUP_NOTE.get(c["group"], "")))
    gs = data["glob"]
    if gs["ok"]:
        out.append("GLOB: ok (CLAUDE_CODE_GLOB_NO_IGNORE=false; Glob skips gitignored files)")
    else:
        n, ex = len(gs["ignored"]), ", ".join(gs["ignored"][:3])
        if data["git"]:
            out.append("GLOB: lists gitignored files{}; fix: CLAUDE_CODE_GLOB_NO_IGNORE=false via /hetz:setup global".format(
                " ({} path{}, e.g. {})".format(n, "" if n == 1 else "s", ex) if n else ""))
        elif n:
            out.append("GLOB/GREP: no git repo, so .gitignore is not applied; searches may include " + ex)
    if not deny_only:
        mcp = data["mcp"]
        sv = mcp["servers"]
        if sv:
            names = sorted(sv)
            al = [n for n in names if sv[n]["alwaysLoad"]]
            out.append("MCP servers {}: {}{}{}{}; prefer CLIs (gh); disable unused with /mcp".format(
                len(names), ", ".join(names[:6]), " +{}".format(len(names) - 6) if len(names) > 6 else "",
                "; alwaysLoad: " + ", ".join(al) if al else "",
                "; " + mcp["upfront"] if mcp["upfront"] else "; schemas deferred"))
        else:
            out.append("MCP: none in ~/.claude.json or .mcp.json" + ("; " + mcp["upfront"] if mcp["upfront"] else ""))
    if "plugins" in data:
        out.extend(plugin_lines(data["plugins"]))
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT - 1] + ["… {} more lines (use --json)".format(len(out) - MAX_OUT + 1)]
    return "\n".join(out)


def plugin_lines(pl):
    out = []
    if not pl["cli"]:
        out.append("PLUGINS: claude CLI not on PATH")
    elif not pl["plugins"]:
        out.append("PLUGINS: none enabled in settings")
    else:
        parts = ["{} {}".format(p["name"], "?" if p["always_on"] is None else "~{:,}".format(p["always_on"]))
                 for p in pl["plugins"]]
        out.append("PLUGINS always-on tok (claude plugin details; counts user-only skills too): " + " · ".join(parts))
    return out + ["  overlap: " + o for o in pl["overlaps"]]


# ---------- writer ----------

BAK = ".hetz-bak"


def git_state(path):
    """'clean' / 'dirty' for a git-tracked file, None when untracked or git is unavailable."""
    if not shutil.which("git"):
        return None
    d, name = os.path.dirname(path), os.path.basename(path)
    if run(["git", "ls-files", "--error-unmatch", "--", name], d) is None:
        return None
    out = run(["git", "status", "--porcelain", "--", name], d)
    return "dirty" if out is None or out.strip() else "clean"


def make_backup(path):
    """Copy to FILE.hetz-bak, else the next free FILE.hetz-bak.N; never overwrites an earlier backup.
    Reuses an existing backup with identical content. Returns the backup path or None."""
    try:
        with open(path, "rb") as f:
            cur = f.read()
    except OSError:
        return None
    for n in range(100):
        bak = path + BAK + ("" if n == 0 else "." + str(n))
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
            git_exclude(os.path.dirname(path), ["*" + BAK + "*"])
            return bak
        try:
            with open(bak, "rb") as f:
                if f.read() == cur:
                    return bak
        except OSError:
            continue
    return None


def git_exclude(anchor, patterns):
    """Add patterns (relative to the work-tree root) to .git/info/exclude once; never .gitignore."""
    if not shutil.which("git"):
        return False
    ex = (run(["git", "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], anchor) or "").strip()
    if not ex or not os.path.isabs(ex):
        return False
    try:
        old = open(ex, encoding="utf-8").read() if os.path.isfile(ex) else ""
    except OSError:
        return False
    have = {l.strip() for l in old.splitlines()}
    new = [p for p in patterns if p not in have]
    if new:
        os.makedirs(os.path.dirname(ex), exist_ok=True)
        with open(ex, "a", encoding="utf-8") as f:
            f.write(("" if not old or old.endswith("\n") else "\n") + "\n".join(new) + "\n")
    return bool(new)


def blanket(rule):
    """True for rules that would deny (nearly) everything: Read(**), Read(./**), Read(//**), Read(..) ..."""
    core = rule[5:-1].strip()
    for pre in ("//", "~/", "./", "/"):
        if core.startswith(pre):
            core = core[len(pre):]
            break
    return not core.strip("/*. ")


def hidden_source(ctx, scope, rules, files):
    """{rule: [source files it would deny]}; files under build/vendor dirs and *.min.* are not source."""
    src = [f for f in files if not f.endswith("/") and os.path.splitext(f)[1].lower() in SOURCE_EXT
           and ".min." not in f.lower() and not any(p in GEN_DIRS or p in SKIP_DIRS for p in f.split("/")[:-1])]
    hits = {}
    for r in rules:
        m = rule_matchers(ctx, {scope: [r]})
        bad = [f for f in src if is_denied(m, os.path.join(ctx.cwd, f))]
        if bad:
            hits[r] = bad
    return hits


def add_deny(cwd, scope, rules):
    bad = [r for r in rules if not re.match(r"^Read\([^\n()]+\)$", r)]
    if not rules or bad:
        print("refused: only Read(<path>) rules are accepted: " + " ".join(bad)[:200])
        return 2
    wide = [r for r in rules if blanket(r)]
    if wide:
        print("refused: blanket rule would hide the whole project: " + " ".join(wide)[:200])
        return 2
    ctx = Ctx(cwd)
    files = list_files(ctx)[0]
    src = hidden_source(ctx, scope, list(dict.fromkeys(rules)), files)
    if src:
        for r, files in src.items():
            print("refused: {} would hide {} source file(s), e.g. {}".format(r, len(files), ", ".join(files[:3]))[:300])
        print("nothing written; drop those rules (never deny source). Deny it by hand in settings if truly intended.")
        return 2
    rel = ".claude/" + ("settings.local.json" if scope == "local" else "settings.json")
    path = os.path.join(cwd, ".claude", os.path.basename(rel))
    existed = os.path.isfile(path)
    data = load_json(path) if existed else {}
    if not isinstance(data, dict):
        print("refused: {} is not a JSON object; fix it first".format(rel))
        return 1
    perms = data.setdefault("permissions", {})
    if not isinstance(perms, dict) or not isinstance(perms.setdefault("deny", []), list):
        print("refused: permissions.deny is not a list in " + rel)
        return 1
    deny = perms["deny"]
    new = [r for r in dict.fromkeys(rules) if r not in deny]
    if not new:
        print("nothing to add: all {} rules already in {}".format(len(rules), rel))
        return 0
    bak = make_backup(path) if existed and git_state(path) != "clean" else None
    deny.extend(new)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    note = ""
    top = ctx.git["top"] if ctx.git else None
    if top and scope == "local" and run(["git", "check-ignore", "-q", path], cwd) is None:
        if git_exclude(cwd, ["/" + os.path.relpath(path, top).replace(os.sep, "/") + "*"]):
            note = "; added to .git/info/exclude"
    print("added {} Read deny rule(s) to {} ({} already present){}{}".format(
        len(new), rel, len(rules) - len(new), "; backup " + os.path.basename(bak) if bak else "", note))
    for r in new:   # Read(./x/**) / Read(x/**) deny every dir named x, not just the top one
        m = re.match(r"^Read\((?:\./)?([^/*~?\[{][^/*?\[{]*)/\*\*\)$", r)
        if m:
            d = "/" + m.group(1) + "/"
            nested = sorted({("/" + f).split(d)[0].lstrip("/") + d.rstrip("/") for f in files
                             if d in "/" + f and not ("/" + f).startswith(d)})
            if nested:
                print("note: {} also denies {} (any depth); Read(/{}/**) would not".format(
                    r, ", ".join(nested[:3]) + (" +{} more".format(len(nested) - 3) if len(nested) > 3 else ""),
                    m.group(1))[:200])
    print("applies to new sessions; self-test: ask Claude to Read one denied file")
    return 0


def backup(cwd, paths):
    if not paths:
        print("refused: --backup needs file paths")
        return 2
    done, clean, missing = [], [], []
    for p in paths:
        full = os.path.realpath(os.path.join(cwd, os.path.expanduser(p)))
        if not os.path.isfile(full):
            missing.append(p)
            continue
        if git_state(full) == "clean":
            clean.append(p)
            continue
        bak = make_backup(full)
        done.append("{} -> {}".format(p, os.path.basename(bak) if bak else "FAILED (do not edit)"))
    for label, lst in (("backup", done), ("tracked + committed (git is the backup)", clean), ("not found", missing)):
        if lst:
            print("{}: {}".format(label, "; ".join(lst))[:400])
    return 0


def main(argv):
    args, flags, rest = [], set(), []
    for a in argv:   # tolerate "--a --b" passed as one quoted argument
        args.extend(a.split()) if a.startswith("--") else args.append(a)
    cwd, scope, i = os.getcwd(), "local", 0
    while i < len(args):
        a = args[i]
        if a in ("--deny-only", "--plugins", "--plugins-only", "--json", "--add-deny", "--backup"):
            flags.add(a)
        elif a in ("--cwd", "--scope") and i + 1 < len(args):
            if a == "--cwd":
                cwd = args[i + 1]
            else:
                scope = args[i + 1]
            i += 1
        else:
            rest.append(a)
        i += 1
    if "--backup" in flags:
        return backup(os.path.realpath(cwd), rest)
    if "--add-deny" in flags:
        if scope not in ("local", "project"):
            print("refused: --scope must be local or project")
            return 2
        return add_deny(os.path.realpath(cwd), scope, rest)
    if not os.path.isdir(cwd):
        print("slim audit: not a directory: " + cwd)
        return 0
    ctx = Ctx(cwd)
    if "--plugins-only" in flags:
        print("\n".join(plugin_lines(plugin_status(ctx))))
        return 0
    deny_only = "--deny-only" in flags
    data = audit(ctx, deny_only=deny_only, plugins="--plugins" in flags)
    if "--json" in flags:
        print(json.dumps(data, indent=1, sort_keys=True, default=str))
    else:
        print(render(ctx, data, deny_only=deny_only, ignored_args=rest))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:   # never dump contents or tracebacks into the model's context
        print("slim audit failed: {}".format(type(exc).__name__))
        sys.exit(0)
