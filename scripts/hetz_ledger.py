#!/usr/bin/env python3
"""hetz ledger: where Claude Code tokens and list-price dollars went.

Reads ~/.claude/projects/**/*.jsonl (main and subagent transcripts) READ-ONLY and prints
aggregates only: usage numbers, model ids, timestamps, agent types, result lengths and
command family names (a command name, plus a known subcommand; anything that is not a builtin,
known tool, script path or on PATH shows as "(other)"). It never prints prompts, replies,
tool output, file contents or full commands.

  hetz_ledger.py [--since 14d] [--project hetz] [--session 1a2b] [--json]
  hetz_ledger.py --compare 2026-09-01..2026-09-30 2026-10-01..2026-10-17
  hetz_ledger.py --replay-squeeze [--since 30d]     (exit 3 if any KEEP line would be lost)

Dollars are list-price estimates, not a subscription bill: compare relative changes only.
Transcripts under-record output tokens. Python 3 stdlib only.
"""
import argparse
import bisect
import datetime as dt
import functools
import glob
import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import sys
import tempfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_PRICES = os.path.join(REPO, "plugins", "hetz", "hooks", "prices.json")
DEFAULT_SQUEEZE = os.path.join(REPO, "plugins", "hetz", "hooks", "squeeze.py")

RATE_KEYS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
TOKEN_KEYS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
WEB_SEARCH_USD = 0.01          # $10 per 1,000 server-side searches
# usage.speed == "fast", as Claude Code 2.1.272 prices it: $30/$150 for Opus 4.6/4.7, $10/$50 for Opus 4.8/5
# (6x and 2x of their $5/$25 base); every other model at its base price.
FAST_MULT = {"claude-opus-4-6": 6.0, "claude-opus-4-7": 6.0, "claude-opus-4-8": 2.0, "claude-opus-5": 2.0}
GEO_US_MULT = 1.1              # usage.inference_geo == "us": tokens (not web search) cost 1.1x
BREAK_MIN = 20000              # cache_creation above this, after a thread's first request, is an event
REUSE_RATIO = 0.8              # cache_read >= 80% of the previous context: prefix kept, just a big append
GATE_DAYS = 14                 # replay gate: needs at least two weeks of history
GATE_PCT = 15.0                # replay gate: >= 15% of Bash result chars removed
BUCKETS = ((50000, "<50k"), (150000, "50-150k"), (300000, "150-300k"), (600000, "300-600k"), (None, ">600k"))


def _r(i, r, o, w5=None, w1=None):
    return {"input": i, "cache_write_5m": i * 1.25 if w5 is None else w5,
            "cache_write_1h": i * 2 if w1 is None else w1, "cache_read": r, "output": o}


# Same schema as plugins/hetz/hooks/prices.json; used only when that file is missing or invalid.
FALLBACK_PRICES = {
    "_source": "https://platform.claude.com/docs/en/about-claude/pricing",
    "_date": "2026-10-17",
    "_over_100k": {"claude-haiku-5-5": _r(0.5, 0.05, 2.5)},
    "models": {
        "claude-fable-5-1": _r(10, 0.25, 50), "claude-fable-5": _r(10, 1.0, 50),
        "claude-mythos-5-1": _r(10, 0.25, 50), "claude-mythos-5": _r(10, 1.0, 50),
        "claude-opus-5-5": _r(4, 0.20, 20), "claude-opus-5": _r(5, 0.50, 25),
        "claude-opus-4-8": _r(5, 0.50, 25), "claude-opus-4-7": _r(5, 0.50, 25),
        "claude-opus-4-6": _r(5, 0.50, 25), "claude-opus-4-5": _r(5, 0.50, 25),
        "claude-opus-4": _r(15, 1.50, 75),
        "claude-sonnet-5-5": _r(2, 0.20, 10), "claude-sonnet-5": _r(2, 0.20, 10),
        "claude-sonnet-4": _r(3, 0.30, 15), "claude-3-7-sonnet": _r(3, 0.30, 15),
        "claude-haiku-5-5": _r(0.10, 0.01, 0.50), "claude-haiku-4-5": _r(1, 0.10, 5),
        "claude-3-5-haiku": _r(0.8, 0.08, 4),
    },
}


# ---------------------------------------------------------------- small helpers

def parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def local_day(ts):
    return dt.datetime.fromtimestamp(ts).date().isoformat()


def iso(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def num(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def percentile(values, q):
    if not values:
        return 0
    s = sorted(values)
    return s[max(0, min(len(s) - 1, int(math.ceil(q * len(s))) - 1))]


def median(values):
    if not values:
        return 0
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def label(value, limit=40):
    """Metadata names only (model ids, agent types, families): printable, short."""
    text = re.sub(r"[^\x20-\x7e]", "?", str(value or "?"))
    return text if len(text) <= limit else text[:limit - 1] + "~"


def ftok(n):
    n = float(n or 0)
    if n >= 1e6:
        return "%.2fM" % (n / 1e6)
    if n >= 1e3:
        return "%.1fk" % (n / 1e3)
    return "%d" % n


def fusd(x):
    return "$%s" % format(x or 0, ",.2f")


# ---------------------------------------------------------------- time windows

_REL = re.compile(r"^(\d+(?:\.\d+)?)([mhdw])$")


def parse_when(text, end=False, now=None):
    """Nh/Nd/Nw ago, YYYY-MM-DD (local midnight; with end=True the next midnight) or ISO time."""
    text = (text or "").strip()
    if not text:
        return None
    now = time_now() if now is None else now
    m = _REL.match(text)
    if m:
        unit = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
        return now - float(m.group(1)) * unit
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        day = dt.datetime.strptime(text, "%Y-%m-%d")
        if end:
            day += dt.timedelta(days=1)
        return day.timestamp()
    ts = parse_ts(text)
    if ts is None:
        raise ValueError("bad time: %r (use 14d, 12h, 2w or YYYY-MM-DD)" % text)
    return ts


def time_now():
    return dt.datetime.now().timestamp()


def parse_range(text, now=None):
    if ".." not in text:
        raise ValueError("bad range %r: use A..B, e.g. 2026-09-01..2026-09-30 or 30d..14d" % text)
    a, b = text.split("..", 1)
    return parse_when(a, now=now), parse_when(b, end=True, now=now)


class Window:
    def __init__(self, start=None, end=None, project=None, session=None):
        self.start, self.end = start, end
        self.project = (project or "").strip().lower() or None
        self.session = (session or "").strip().lower() or None

    def time_ok(self, ts):
        if self.start is None and self.end is None:
            return True
        if ts is None:
            return False
        return (self.start is None or ts >= self.start) and (self.end is None or ts < self.end)

    def ok(self, ts, session, project_names):
        if not self.time_ok(ts):
            return False
        if self.session and not str(session or "").lower().startswith(self.session):
            return False
        if self.project and not any(self.project in (n or "").lower() for n in project_names):
            return False
        return True

    def describe(self):
        return {"since": iso(self.start) if self.start else None, "until": iso(self.end) if self.end else None,
                "project": self.project, "session": self.session}


def project_needle(text):
    """--project accepts a name fragment or a path (encoded the way ~/.claude/projects names dirs)."""
    if text and (os.sep in text or text.startswith("~") or text == "."):
        return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.expanduser(text)))
    return text


# ---------------------------------------------------------------- prices

class Prices:
    def __init__(self, path=None):
        data, self.source = None, "built-in fallback"
        path = path or DEFAULT_PRICES
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("models"), dict) and data["models"]:
                self.source = path
            else:
                data = None
        except Exception:
            data = None
        data = data or FALLBACK_PRICES
        self.date = data.get("_date")
        self.models = {k.lower(): v for k, v in data["models"].items() if isinstance(v, dict)}
        over = data.get("_over_100k") if isinstance(data.get("_over_100k"), dict) else {}
        self.over = {k.lower(): v for k, v in over.items() if isinstance(v, dict)}
        self._cache = {}

    @staticmethod
    def normalize(model):
        m = str(model or "").lower()
        i = m.find("claude-")
        return m[i:] if i > 0 else m

    def match(self, model):
        """Longest prices key that is a prefix of the model id, or None."""
        m = self.normalize(model)
        if m not in self._cache:
            keys = [k for k in self.models if m.startswith(k)]
            self._cache[m] = max(keys, key=len) if keys else None
        return self._cache[m]

    def rates(self, model, context=0):
        key = self.match(model)
        if key is None:
            return None
        rates = self.models[key]
        if context > 100000 and key in self.over:
            rates = dict(rates, **self.over[key])
        return rates


def rate(rates, key):
    try:
        return float(rates.get(key) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


# ---------------------------------------------------------------- command families

_WORD2 = re.compile(r"^[a-z][a-z0-9._:-]{0,24}$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.+-]{0,31}$")
_HEXISH = re.compile(r"^[0-9a-fA-F-]{8,}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
SETUP = {"cd", "pushd", "popd", "export", "set", "source", ".", "unset", "true", ":", "ulimit", "umask", "shopt",
         "local", "declare", "typeset", "readonly", "alias", "trap", "wait"}
KEYWORD_HEAD = {"for", "while", "until", "if", "elif", "case", "select", "function", "esac", "done", "fi"}
KEYWORD_PREFIX = {"do", "then", "else", "!", "{", "}", "time"}
WRAPPERS = {"sudo", "env", "nice", "nohup", "command", "builtin", "exec", "timeout", "caffeinate", "stdbuf", "xargs"}
WRAPPER_ARGS = {   # wrapper options that take a separate argument word (sudo -u root ls -> ls)
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-U", "-r", "-t", "-D", "-R", "-T", "--user", "--group", "--host",
             "--prompt", "--chdir", "--other-user", "--role", "--type", "--close-from", "--chroot"},
    "env": {"-u", "-C", "-S", "-P", "--unset", "--chdir", "--split-string"},
    "nice": {"-n", "--adjustment"}, "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-I", "-J", "-L", "-n", "-P", "-R", "-S", "-s", "-d", "-E", "-a", "--max-args", "--max-procs",
              "--delimiter", "--arg-file", "--max-lines", "--replace", "--eof"},
    "caffeinate": {"-t", "-w"}, "stdbuf": {"-i", "-o", "-e"}, "exec": {"-a"}}
SUBCOMMANDS = {"git", "npm", "npx", "pnpm", "yarn", "bun", "bunx", "cargo", "go", "docker", "podman", "kubectl",
               "gh", "uv", "uvx", "pip", "pip3", "poetry", "pipx", "brew", "claude", "make", "just", "dotnet",
               "mvn", "gradle", "gradlew", "terraform", "helm", "deno", "bundle", "rake", "rails", "swift",
               "xcodebuild", "conda", "rustup", "apt", "apt-get", "nx", "turbo", "mix", "flutter", "dart",
               "composer", "aws", "gcloud", "az", "systemctl", "launchctl", "tmux", "kubectx", "go-task"}
BUILTINS = {"echo", "printf", "read", "test", "pwd", "exit", "return", "eval", "shift", "getopts", "type", "hash",
            "kill", "jobs", "fg", "bg", "times", "let", "unalias", "enable", "help", "history", "mapfile",
            "readarray", "caller", "compgen", "complete", "dirs", "disown", "false", "break", "continue"}
KNOWN = BUILTINS | SUBCOMMANDS | {
    "rg", "fd", "jq", "yq", "bat", "node", "tsc", "eslint", "prettier", "jest", "vitest", "vite", "next", "webpack",
    "playwright", "pytest", "py.test", "ruff", "mypy", "black", "isort", "flake8", "pylint", "tox", "nox",
    "pre-commit", "coverage", "pyright", "cmake", "ninja", "gmake", "mvnw", "docker-compose", "rtk", "rustc",
    "clang", "gcc", "javac", "java", "ruby", "perl", "php", "lua", "sqlite3", "psql", "redis-cli", "ffmpeg"}
_SCRIPT_EXT = re.compile(r"\.(?:sh|bash|zsh|fish|py|js|mjs|cjs|ts|rb|pl|php|lua|ps1|bat|cmd|exe)$")
_CONT = re.compile(r"\\\r?\n")
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_.-]*)\1")


def _strip_heredocs(command):
    """Drop here-document bodies (they are data, not commands)."""
    out, ends = [], []
    for line in command.split("\n"):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        out.append(line)
        ends.extend(m.group(2) for m in _HEREDOC.finditer(line))
    return "\n".join(out)


def _simple_commands(command):
    """Quote-aware split into simple commands (word lists); stops quietly at the first lexing error.
    Skips here-doc bodies, array literals (a=( ... )), case ... esac and function-definition names."""
    lex = shlex.shlex(_strip_heredocs(_CONT.sub(" ", command)), posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    words, skip, depth, case = [], False, 0, 0
    try:
        for tok in iter(lex.get_token, None):
            punct = bool(tok) and not tok.strip("();<>|&\n")
            if depth:                            # inside name=( ... )
                if punct:
                    depth += tok.count("(") - tok.count(")")
                continue
            if case:                             # case patterns and arms
                case += (tok == "case") - (tok == "esac")
                continue
            if tok == "case" and all(w in KEYWORD_PREFIX for w in words):
                case, words = 1, []
                continue
            if punct and tok.startswith("(") and words:
                last = words[-1]
                if last.endswith("=") and _ENV.match(last):
                    depth = tok.count("(") - tok.count(")")
                    continue
                if not last.endswith("$") and words[0] not in KEYWORD_HEAD | KEYWORD_PREFIX | {"function"}:
                    words = []                   # name() { ... }: a definition, not a command
                    continue
            if punct and not tok.strip("();|&\n"):
                yield words
                words = []
            elif punct and not tok.strip("<>&|"):
                skip = True                      # redirection operator: drop its target word
            elif skip:
                skip = False
            else:
                words.append(tok)
    except ValueError:
        pass
    yield words


def _drop_prefix(words):
    """Strip env assignments, keywords and wrappers (with their option arguments) before the command."""
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        base = os.path.basename(w)
        if base in WRAPPERS:
            takes = WRAPPER_ARGS.get(base, ())
            i += 1
            while i < n and words[i].startswith("-") and words[i] != "-":
                if words[i] == "--":
                    i += 1
                    break
                i += 2 if words[i] in takes else 1
            if base == "timeout" and i < n and re.match(r"^\d+(?:\.\d+)?[smhd]?$", words[i]):
                i += 1
        elif _ENV.match(w) or w in KEYWORD_PREFIX or re.match(r"^-|^\d+$", w):
            i += 1
        else:
            break
    return words[i:]


@functools.lru_cache(maxsize=4096)
def _is_command(word, first):
    """Backstop against printing data words: a builtin, a known tool, a script path or something on PATH."""
    return first in KNOWN or "/" in word or bool(_SCRIPT_EXT.search(first)) or \
        bool(re.match(r"^python[\d.]*$", first)) or shutil.which(first) is not None


def family(command):
    """'git status', 'npm test', 'python3 -m pytest', 'cat', ... a command name (+ known subcommand) only."""
    for words in _simple_commands(str(command or "")):
        if words and words[0] in KEYWORD_HEAD:
            continue
        words = _drop_prefix(words)
        if not words:
            continue
        first = os.path.basename(words[0]) or words[0]
        if first in SETUP:
            continue
        if first in ("[", "[["):
            return "test"
        if first.startswith("$"):
            return "(variable)"
        if not _NAME.match(first) or _HEXISH.match(first) or not _is_command(words[0], first):
            return "(other)"
        rest = words[1:3]
        if first.startswith("python") and len(rest) == 2 and rest[0] == "-m" and _WORD2.match(rest[1]):
            return "%s -m %s" % (first, rest[1])
        if first in SUBCOMMANDS and rest and _WORD2.match(rest[0]):
            return "%s %s" % (first, rest[0])
        return first
    return "(none)"


def cmd_head(command):
    """The squeeze.tsv cmd_head column: whitespace collapsed, first 80 chars (kept in memory only)."""
    return " ".join(str(command).split())[:80]


def _sid(value):
    """An id usable as a key: str or int only (malformed records carry lists, dicts, ...)."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value)


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


# ---------------------------------------------------------------- squeeze replay

# CSI, OSC (hyperlinks, titles) and other ESC sequences: the same set squeeze strips.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_RUN_MARK = re.compile(r" \(×\d+\)\s*$")       # squeeze folds >= 3 identical lines into "line (×N)"


def _rendered(text, ansi_res=(_ANSI,)):
    """Lines as squeeze sees them: escapes removed, split on '\\n' only, last '\\r' segment wins."""
    for rx in ansi_res:
        text = rx.sub("", text)
    for line in text.split("\n"):
        if "\r" in line:
            parts = [p for p in line.split("\r") if p]
            line = parts[-1] if parts else ""
        yield line


def keep_lost(keep_re, text_in, text_out, ansi_res=(_ANSI,)):
    """Input lines matching KEEP_RE that no longer appear in the output (normalised as squeeze renders)."""
    out = set()
    for line in _rendered(text_out, ansi_res):
        out.add(line.strip())
        out.add(_RUN_MARK.sub("", line).strip())
    return sum(1 for line in _rendered(text_in, ansi_res) if keep_re.search(line) and line.strip() not in out)


def load_squeeze(path):
    spec = importlib.util.spec_from_file_location("hetz_squeeze_replay", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "squeeze", None)) or not hasattr(getattr(mod, "KEEP_RE", None), "search"):
        raise ImportError("%s lacks squeeze() or KEEP_RE" % path)
    return mod


class Replay:
    def __init__(self, path):
        self.path = path
        self.mod = load_squeeze(path)
        try:   # the live hook appends a footer (raw-log path) that squeeze() does not return
            self.footer = max(0, int(getattr(self.mod, "FOOTER_EST", 0) or 0))
        except (TypeError, ValueError):
            self.footer = 0
        own = getattr(self.mod, "_ANSI_RE", None)        # squeeze's own escape regex, plus ours
        self.ansi = ((own,) if hasattr(own, "sub") else ()) + (_ANSI,)
        self.fam = defaultdict(lambda: defaultdict(int))
        self.lost_events = []
        self.first = self.last = None

    def feed(self, command, fam, text, is_error, ts, session, tool_use_id):
        st = self.fam[fam]
        st["calls"] += 1
        st["chars_in"] += len(text)
        if ts:
            self.first = ts if self.first is None else min(self.first, ts)
            self.last = ts if self.last is None else max(self.last, ts)
        if is_error:                      # PostToolUse squeeze only sees successful runs
            st["errors"] += 1
            st["chars_out"] += len(text)
            return
        try:
            res = self.mod.squeeze(command, text, "")
            if res is not None and not isinstance(res, dict):
                raise TypeError("squeeze returned %s" % type(res).__name__)
        except (Exception, SystemExit):
            st["exceptions"] += 1
            st["chars_out"] += len(text)
            return
        if res is None:
            st["passthrough"] += 1
            st["chars_out"] += len(text)
            return
        out, err, tip = (str(res.get(k) or "") for k in ("stdout", "stderr", "tip"))
        shown = out + ("\n" + err if err else "")
        size = len(out) + len(err) + (len(tip) + 1 if tip and tip not in shown else 0) + self.footer
        lost = keep_lost(self.mod.KEEP_RE, text, shown + "\n" + tip, self.ansi)
        st["squeezed"] += 1
        st["chars_out"] += size
        st["keep_lost"] += lost
        if lost:
            self.lost_events.append({"session": str(session)[:8], "tool_use_id": label(tool_use_id, 48),
                                     "family": fam, "lines": lost})

    def summary(self, total_bash_chars, top):
        tot = defaultdict(int)
        for st in self.fam.values():
            for k, v in st.items():
                tot[k] += v
        saved = tot["chars_in"] - tot["chars_out"]
        span = (self.last - self.first) / 86400 if self.first and self.last else 0
        fams = sorted(self.fam.items(), key=lambda kv: (-(kv[1]["chars_in"] - kv[1]["chars_out"]), -kv[1]["chars_in"]))
        rows = [{"family": f, "calls": s["calls"], "squeezed": s["squeezed"], "errors": s["errors"],
                 "chars_in": s["chars_in"], "chars_saved": s["chars_in"] - s["chars_out"],
                 "saved_pct": round(pct(s["chars_in"] - s["chars_out"], s["chars_in"]), 1),
                 "keep_lost": s["keep_lost"], "exceptions": s["exceptions"]} for f, s in fams]
        saved_pct = pct(saved, tot["chars_in"])
        reasons = []
        if tot["keep_lost"]:
            reasons.append("KEEP lines lost")
        if saved_pct < GATE_PCT:
            reasons.append("saved < %d%%" % GATE_PCT)
        if span < GATE_DAYS:
            reasons.append("history < %d days" % GATE_DAYS)
        return {"squeeze_path": self.path, "results": tot["calls"], "errors_skipped": tot["errors"],
                "squeezed": tot["squeezed"], "passthrough": tot["passthrough"], "exceptions": tot["exceptions"],
                "chars_in": tot["chars_in"], "chars_out": tot["chars_out"], "chars_saved": saved,
                "saved_pct": round(saved_pct, 2), "keep_lost": tot["keep_lost"], "span_days": round(span, 1),
                "bash_chars_in_window": total_bash_chars,
                "gate": {"pass": not reasons, "reasons": reasons},
                "by_family": rows[:max(top, 1)], "keep_lost_events": self.lost_events[:50]}


# ---------------------------------------------------------------- scanning

class Req:
    __slots__ = ("key", "ts", "model", "inp", "cr", "cw5", "cw1", "out", "web", "speed", "geo", "adv", "adv_cost",
                 "session", "project", "thread", "sub", "agent", "version", "entry", "effort", "api_reason", "cost",
                 "parts")

    @property
    def context(self):
        return self.inp + self.cr + self.cw5 + self.cw1


def usage_numbers(u):
    inp, cr, cw = num(u.get("input_tokens")), num(u.get("cache_read_input_tokens")), num(u.get("cache_creation_input_tokens"))
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    cw1 = min(cw, num(cc.get("ephemeral_1h_input_tokens")))
    cw5 = cw - cw1                      # unsplit writes count as 5m
    stu = u.get("server_tool_use") if isinstance(u.get("server_tool_use"), dict) else {}
    return inp, cr, cw5, cw1, num(u.get("output_tokens")), num(stu.get("web_search_requests"))


def advisor_usage(u):
    """usage.iterations of type advisor_message: billed on top of the top-level usage at the advisor's model."""
    out = []
    for it in u.get("iterations") if isinstance(u.get("iterations"), list) else ():
        if isinstance(it, dict) and it.get("type") == "advisor_message":
            inp, cr, cw5, cw1, o, _ = usage_numbers(it)
            out.append((str(it.get("model") or "?"), dict(zip(TOKEN_KEYS, (inp, cw5, cw1, cr, o)))))
    return out


class BashRes:
    __slots__ = ("ts", "session", "project", "family", "chars", "is_error", "sub", "thread", "head")

    def __init__(self, *values):
        for k, v in zip(self.__slots__, values):
            setattr(self, k, v)


class Data:
    def __init__(self):
        self.reqs = {}                    # message id -> Req (deduped across chunk records and files)
        self.threads = defaultdict(list)  # transcript thread -> [("r", key) | ("c", kind)]
        self.compactions = defaultdict(list)  # transcript thread -> [ts of compaction / microcompaction]
        self.bash = []                    # BashRes per Bash tool result
        self.projects = {}                # project dir -> display name (basename of cwd)
        self.files = self.bad_lines = 0


def read_meta_type(path):
    try:
        with open(path[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as f:
            meta = json.load(f)
        return meta.get("agentType") if isinstance(meta, dict) else None
    except Exception:
        return None


# bench/run.py puts every fixture under a dir containing this marker, so its project dir names carry it.
BENCH_MARKER = "hetz-bench"


def scan(root, data=None, min_mtime=None, replay=None, window=None):
    data = data or Data()
    seen_tools = set()
    want_bench = bool(window and window.project and BENCH_MARKER in window.project)
    for path in sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)):
        if not want_bench and BENCH_MARKER in os.path.relpath(path, root).split(os.sep)[0]:
            continue  # bench/run.py fixture sessions are not real work (ask for them with --project hetz-bench)
        try:
            if min_mtime is not None and os.path.getmtime(path) < min_mtime:
                continue
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        data.files += 1
        parts = os.path.relpath(path, root).split(os.sep)
        project = parts[0] if len(parts) > 1 else "(root)"
        sub_path = "subagents" in parts or os.path.basename(path).startswith("agent-")
        file_session = parts[1] if sub_path and len(parts) > 2 and parts[2] == "subagents" \
            else os.path.basename(path)[:-len(".jsonl")]
        meta_type = read_meta_type(path) if sub_path else None
        ctx = {"path": path, "project": project, "sub_path": sub_path, "file_session": file_session,
               "meta_type": meta_type, "pending": {}, "seen_tools": seen_tools}
        with fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    data.bad_lines += 1
                    continue
                if not isinstance(rec, dict):
                    continue
                try:
                    _scan_record(data, rec, ctx, replay, window)
                except Exception:         # one malformed record must not stop the report
                    data.bad_lines += 1
    return data


def _scan_record(data, rec, ctx, replay, window):
    path, project, sub_path = ctx["path"], ctx["project"], ctx["sub_path"]
    pending, seen_tools = ctx["pending"], ctx["seen_tools"]
    kind = rec.get("type")
    side = rec.get("isSidechain") is True
    thread = path if sub_path or not side else path + "#" + (_sid(rec.get("agentId")) or "side")
    session = _sid(rec.get("sessionId")) or ctx["file_session"]
    ts = parse_ts(rec.get("timestamp"))
    if project not in data.projects and isinstance(rec.get("cwd"), str) and not (sub_path or side):
        data.projects[project] = os.path.basename(rec["cwd"].rstrip("/")) or project
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    content = msg.get("content") if isinstance(msg.get("content"), list) else []
    if kind == "assistant":
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                cmd = b["input"].get("command") if isinstance(b.get("input"), dict) else None
                tid = _sid(b.get("id"))
                if isinstance(cmd, str) and tid:
                    pending[tid] = cmd
        usage = msg.get("usage")
        if not isinstance(usage, dict) or msg.get("model") == "<synthetic>" or rec.get("isApiErrorMessage"):
            return
        inp, cr, cw5, cw1, out, web = usage_numbers(usage)
        adv = advisor_usage(usage)
        key = _sid(msg.get("id")) or _sid(rec.get("requestId")) or _sid(rec.get("uuid")) \
            or "%s:%d" % (path, len(data.reqs))
        r = data.reqs.get(key)
        if r is not None:     # later chunk of the same response: same input side, growing output
            r.inp, r.cr, r.cw5, r.cw1 = max(r.inp, inp), max(r.cr, cr), max(r.cw5, cw5), max(r.cw1, cw1)
            r.out, r.web = max(r.out, out), max(r.web, web)
            r.adv = adv or r.adv
            return
        if not (inp or cr or cw5 or cw1 or out or adv):
            return
        r = Req()
        r.key, r.ts, r.model = key, ts, str(msg.get("model") or "?")
        r.inp, r.cr, r.cw5, r.cw1, r.out, r.web, r.adv = inp, cr, cw5, cw1, out, web, adv
        r.speed, r.geo = usage.get("speed"), usage.get("inference_geo")
        r.session, r.project, r.thread = session, project, thread
        r.sub = sub_path or side
        r.agent = (rec.get("attributionAgent") or ctx["meta_type"] or "subagent") if r.sub else None
        r.version, r.entry, r.effort = rec.get("version"), rec.get("entrypoint"), rec.get("effort")
        diag = msg.get("diagnostics") if isinstance(msg.get("diagnostics"), dict) else {}
        miss = diag.get("cache_miss_reason") if isinstance(diag.get("cache_miss_reason"), dict) else {}
        r.api_reason = _sid(miss.get("type"))
        data.reqs[key] = r
        data.threads[thread].append(("r", key))
    elif kind == "user":
        if rec.get("isCompactSummary"):
            data.threads[thread].append(("c", "compaction"))
            data.compactions[thread].append(ts)
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            tid = _sid(b.get("tool_use_id"))
            cmd = pending.pop(tid, None) if tid else None
            if cmd is None or tid in seen_tools:
                continue
            seen_tools.add(tid)
            text, fam = result_text(b.get("content")), family(cmd)
            is_error = b.get("is_error") is True
            data.bash.append(BashRes(ts, session, project, fam, len(text), is_error, sub_path or side, thread,
                                     hash(cmd_head(cmd))))
            if replay is not None and (window is None or window.ok(ts, session, (project, data.projects.get(project)))):
                replay.feed(cmd, fam, text, is_error, ts, session, tid)
    elif kind == "system" and rec.get("subtype") in ("compact_boundary", "microcompact_boundary"):
        data.threads[thread].append(("c", "compaction"))
        data.compactions[thread].append(ts)


def canonical(model):
    """Model id without provider prefix, date or version suffix: us.anthropic.claude-x-4-6-20260101-v1:0 -> claude-x-4-6."""
    m = Prices.normalize(model)
    m = re.sub(r"\[.*\]$", "", m)
    m = re.sub(r"(?:@|-v\d+(?::\d+)?$).*$", "", m)
    return re.sub(r"-\d{8}$", "", m)


def fast_mult(model):
    return FAST_MULT.get(canonical(model), 1.0)


def price_all(data, prices):
    unpriced = defaultdict(int)
    for r in data.reqs.values():
        rates = prices.rates(r.model, r.context)
        geo = GEO_US_MULT if r.geo == "us" else 1.0
        mult = (fast_mult(r.model) if r.speed == "fast" else 1.0) * geo
        tokens = dict(zip(TOKEN_KEYS, (r.inp, r.cw5, r.cw1, r.cr, r.out)))
        if rates is None:
            unpriced[r.model] += 1
            r.parts = dict.fromkeys(TOKEN_KEYS, 0.0)
        else:
            r.parts = {k: tokens[k] * rate(rates, k) * mult / 1e6 for k in TOKEN_KEYS}
        r.adv_cost = defaultdict(float)
        for model, toks in r.adv or ():
            arates = prices.rates(model, toks["input"] + toks["cache_read"] + toks["cache_write_5m"] + toks["cache_write_1h"])
            if arates is None:
                unpriced[model] += 1
                continue
            for k in TOKEN_KEYS:
                usd = toks[k] * rate(arates, k) * geo / 1e6
                r.parts[k] += usd
                r.adv_cost[model] += usd
        r.parts["web_search"] = r.web * WEB_SEARCH_USD
        r.cost = sum(r.parts.values())
    return dict(unpriced)


# ---------------------------------------------------------------- cache breaks

def find_breaks(data):
    """Large cache writes after a thread's first request, with the likely cause."""
    events = []
    for thread, evs in data.threads.items():
        prev, compacted, uses_1h = None, False, False
        for kind, val in evs:
            if kind == "c":
                compacted = True
                continue
            r = data.reqs.get(val)
            if r is None or r.thread != thread:
                continue
            cw = r.cw5 + r.cw1
            if prev is not None and cw > BREAK_MIN:
                gap = (r.ts - prev.ts) if (r.ts and prev.ts) else None
                ttl = 3600 if uses_1h else 300
                prev_ctx = prev.cr + prev.cw5 + prev.cw1
                if prev_ctx and r.cr >= REUSE_RATIO * prev_ctx and r.model == prev.model:
                    cause = "big_append"   # prefix reused; new bulk content (tool result, file) was written
                elif r.model != prev.model:
                    cause = "model_change"
                elif compacted:
                    cause = "compaction"
                elif gap is not None and gap > ttl:
                    cause = "ttl_expired_%s" % ("1h" if uses_1h else "5m")
                elif (r.version and prev.version and r.version != prev.version) or \
                        (r.entry and prev.entry and r.entry != prev.entry):
                    cause = "resume"
                elif r.effort and prev.effort and r.effort != prev.effort:
                    cause = "effort_change"
                elif r.api_reason:
                    cause = "api:%s" % label(r.api_reason, 32)
                else:
                    cause = "other"
                events.append({"req": r, "cause": cause, "gap_min": round(gap / 60, 1) if gap is not None else None,
                               "tokens": cw})
            uses_1h = uses_1h or r.cw1 > 0
            prev, compacted = r, False
    return events


# ---------------------------------------------------------------- squeeze.tsv (realized savings)

def squeeze_tsv_paths(override=None):
    if override:
        return [override] if os.path.isfile(override) else []
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    cands = []
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        cands.append(os.path.join(os.environ["CLAUDE_PLUGIN_DATA"], "squeeze.tsv"))
    for p in sorted(glob.glob(os.path.join(cfg, "plugins", "data", "hetz-*", "squeeze.tsv"))):
        if not os.path.basename(os.path.dirname(p)).startswith("hetz-crew"):
            cands.append(p)
    cands.append(os.path.join(cfg, "hetz", "data", "squeeze.tsv"))
    out = []
    for p in cands:
        if os.path.isfile(p) and os.path.realpath(p) not in [os.path.realpath(q) for q in out]:
            out.append(p)
    return out


def _tsv_ts(text):
    try:
        return float(text)
    except ValueError:
        return parse_ts(text)


MATCH_SLACK = 60.0    # seconds between a squeeze.tsv row and the transcript's tool_result record


def _match_result(cands, stamps, ts, head, fam, used):
    """The Bash result a squeeze.tsv row came from: same session, nearest in time, same cmd_head (else family)."""
    best = None
    for b in cands[bisect.bisect_left(stamps, ts - MATCH_SLACK):bisect.bisect_right(stamps, ts + MATCH_SLACK)]:
        if id(b) in used:
            continue
        rank = 0 if b.head == head else 1 if b.family == fam else None
        if rank is not None and (best is None or (rank, abs(b.ts - ts)) < best[0]):
            best = ((rank, abs(b.ts - ts)), b)
    return best[1] if best else None


def squeeze_realized(paths, data, window, prices, top):
    """chars removed by the live hook (~4 chars/token) x (one cache write + a cache read per later request in the
    thread that saw the result, up to its next compaction). Hooks inside subagents report the parent's session_id,
    so each row is matched to its transcript result to find the thread; an unmatched row is credited one cache
    write at the session's next request (a lower bound)."""
    if not paths:
        return None
    by_thread, by_session = defaultdict(list), defaultdict(list)
    for r in data.reqs.values():
        if r.ts:
            by_thread[r.thread].append(r)
            by_session[r.session].append(r)
    for lst in list(by_thread.values()) + list(by_session.values()):
        lst.sort(key=lambda r: r.ts)
    uses_1h = {t: any(r.cw1 for r in lst) for t, lst in by_thread.items()}
    results = defaultdict(list)
    for b in data.bash:
        if b.ts is not None and not b.is_error:     # squeeze runs on successful PostToolUse only
            results[b.session].append(b)
    for lst in results.values():
        lst.sort(key=lambda b: b.ts)
    res_ts = {s: [b.ts for b in lst] for s, lst in results.items()}
    proj_of = {}
    for r in data.reqs.values():
        proj_of.setdefault(r.session, r.project)
    proj_names = data.projects

    def write_rate(r):
        rates = prices.rates(r.model, r.context) or {}
        return rate(rates, "cache_write_1h" if uses_1h.get(r.thread) else "cache_write_5m")

    used = set()
    fam = defaultdict(lambda: [0, 0, 0, 0.0])
    rows = bad = matched = 0
    for path in paths:
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 5:
                    bad += 1
                    continue
                ts, session = _tsv_ts(cols[0]), cols[1]
                try:
                    cin, cout = int(cols[3]), int(cols[4])
                except ValueError:
                    bad += 1
                    continue
                names = (proj_of[session], proj_names.get(proj_of[session])) if session in proj_of else ()
                if not window.ok(ts, session, names):
                    continue
                rows += 1
                removed = max(0, cin - cout)
                mtok = removed / 4.0 / 1e6
                usd = 0.0
                f = family(cols[2])
                res = _match_result(results[session], res_ts[session], ts, hash(cols[2]), f, used) \
                    if ts is not None and session in results else None
                if res is not None:
                    matched += 1
                    used.add(id(res))
                    start = max(ts, res.ts)
                    stop = min((c for c in data.compactions.get(res.thread, ()) if c and c > start), default=None)
                    later = [r for r in by_thread.get(res.thread, ()) if r.ts > start and (stop is None or r.ts < stop)]
                    if later:
                        usd = mtok * (write_rate(later[0]) +
                                      sum(rate(prices.rates(r.model, r.context) or {}, "cache_read") for r in later[1:]))
                elif ts is not None:
                    nxt = next((r for r in by_session.get(session, ()) if r.ts > ts), None)
                    if nxt is not None:
                        usd = mtok * write_rate(nxt)
                st = fam[f]
                st[0] += 1
                st[1] += cin
                st[2] += removed
                st[3] += usd
    tot_in = sum(v[1] for v in fam.values())
    tot_rm = sum(v[2] for v in fam.values())
    return {"paths": paths, "rows": rows, "bad_rows": bad, "matched_rows": matched, "unmatched_rows": rows - matched,
            "lower_bound": rows > matched,
            "chars_in": tot_in, "chars_removed": tot_rm,
            "removed_pct": round(pct(tot_rm, tot_in), 2), "usd_saved_est": round(sum(v[3] for v in fam.values()), 4),
            "by_family": [{"family": f, "calls": v[0], "chars_in": v[1], "chars_removed": v[2], "usd_saved_est": round(v[3], 4)}
                          for f, v in sorted(fam.items(), key=lambda kv: -kv[1][2])[:top]]}


# ---------------------------------------------------------------- report

def build_report(data, prices, window, top=10, unpriced=None, breaks=None, tsv_paths=None):
    proj_names = data.projects
    reqs = [r for r in data.reqs.values() if window.ok(r.ts, r.session, (r.project, proj_names.get(r.project)))]
    total = sum(r.cost for r in reqs)
    n = len(reqs)

    split = {}
    tok_of = {"input": "inp", "cache_write_5m": "cw5", "cache_write_1h": "cw1", "cache_read": "cr", "output": "out"}
    for k in TOKEN_KEYS:
        c = sum(r.parts[k] for r in reqs)
        toks = sum(getattr(r, tok_of[k]) + sum(t[k] for _, t in r.adv or ()) for r in reqs)
        split[k] = {"tokens": toks, "cost": round(c, 4), "share": round(pct(c, total), 2)}
    web = sum(r.parts["web_search"] for r in reqs)
    split["web_search"] = {"tokens": sum(r.web for r in reqs), "cost": round(web, 4), "share": round(pct(web, total), 2)}

    by_model = defaultdict(lambda: [0, 0.0])
    for r in reqs:
        by_model[r.model][0] += 1
        by_model[r.model][1] += r.cost - sum(r.adv_cost.values())
        for m, c in r.adv_cost.items():     # advisor turns count under the advisor's model
            by_model[m][1] += c
    models = [{"model": label(m), "requests": v[0], "cost": round(v[1], 4), "share": round(pct(v[1], total), 2)}
              for m, v in sorted(by_model.items(), key=lambda kv: -kv[1][1])]

    bucket_stats = {name: [0, 0.0] for _, name in BUCKETS}
    for r in reqs:
        ctx = r.context
        name = next(nm for lim, nm in BUCKETS if lim is None or ctx < lim)
        bucket_stats[name][0] += 1
        bucket_stats[name][1] += r.cost
    buckets = [{"bucket": nm, "requests": bucket_stats[nm][0], "cost": round(bucket_stats[nm][1], 4),
                "share": round(pct(bucket_stats[nm][1], total), 2)} for _, nm in BUCKETS]
    ctxs = [r.context for r in reqs]
    main_ctx = [r.context for r in reqs if not r.sub]

    # cache breaks
    in_win = {id(r) for r in reqs}
    evs = [e for e in (breaks or []) if id(e["req"]) in in_win]
    causes = defaultdict(lambda: [0, 0, 0.0])
    for e in evs:
        r = e["req"]
        e["cost"] = r.parts["cache_write_5m"] + r.parts["cache_write_1h"]
        causes[e["cause"]][0] += 1
        causes[e["cause"]][1] += e["tokens"]
        causes[e["cause"]][2] += e["cost"]
    real = [e for e in evs if e["cause"] != "big_append"]
    top_ev = sorted(real, key=lambda e: -e["cost"])[:top]

    # subagents
    sub_reqs = [r for r in reqs if r.sub]
    sub_cost = sum(r.cost for r in sub_reqs)
    first_in_thread = {}
    for r in sorted(sub_reqs, key=lambda r: (r.ts or 0)):
        first_in_thread.setdefault(r.thread, r)
    st = defaultdict(lambda: {"runs": set(), "requests": 0, "cost": 0.0, "cold_ctx": [], "cold_cost": 0.0})
    for r in sub_reqs:
        s = st[(label(r.agent), label(r.model))]
        s["runs"].add(r.thread)
        s["requests"] += 1
        s["cost"] += r.cost
    for r in first_in_thread.values():
        s = st[(label(r.agent), label(r.model))]
        s["cold_ctx"].append(r.context)
        s["cold_cost"] += r.cost
    subs = [{"agent": a, "model": m, "runs": len(s["runs"]), "requests": s["requests"], "cost": round(s["cost"], 4),
             "share": round(pct(s["cost"], total), 2),
             "cold_start_tokens_avg": int(sum(s["cold_ctx"]) / len(s["cold_ctx"])) if s["cold_ctx"] else 0,
             "cold_start_cost": round(s["cold_cost"], 4)}
            for (a, m), s in sorted(st.items(), key=lambda kv: -kv[1]["cost"])]

    # bash
    bash = [b for b in data.bash if window.ok(b.ts, b.session, (b.project, proj_names.get(b.project)))]
    fam = defaultdict(lambda: [0, 0, 0, 0, 0])
    for b in bash:
        s = fam[b.family]
        s[0] += 1
        s[1] += b.chars
        s[2] = max(s[2], b.chars)
        s[3] += b.chars > 2000
        s[4] += b.is_error
    bash_chars = sum(b.chars for b in bash)
    fams = [{"family": f, "calls": s[0], "chars": s[1], "share": round(pct(s[1], bash_chars), 2),
             "avg": int(s[1] / s[0]) if s[0] else 0, "max": s[2], "over_2k": s[3], "errors": s[4]}
            for f, s in sorted(fam.items(), key=lambda kv: -kv[1][1])]

    # sessions, days, projects
    sess = defaultdict(lambda: {"cost": 0.0, "requests": 0, "first": None, "project": None})
    for r in reqs:
        s = sess[r.session]
        s["cost"] += r.cost
        s["requests"] += 1
        s["project"] = s["project"] or r.project
        if r.ts and (s["first"] is None or r.ts < s["first"]):
            s["first"] = r.ts
    costs = [s["cost"] for s in sess.values()]
    over5 = [c for c in costs if c > 5]
    days = sorted({local_day(r.ts) for r in reqs if r.ts})
    proj = defaultdict(lambda: [0.0, set()])
    for r in reqs:
        proj[r.project][0] += r.cost
        proj[r.project][1].add(r.session)
    projects = [{"project": label(proj_names.get(p, p)), "cost": round(v[0], 4), "sessions": len(v[1]),
                 "share": round(pct(v[0], total), 2)} for p, v in sorted(proj.items(), key=lambda kv: -kv[1][0])]
    stamps = [r.ts for r in reqs if r.ts]

    report = {
        "window": window.describe(),
        "data_span": {"first": iso(min(stamps)) if stamps else None, "last": iso(max(stamps)) if stamps else None},
        "prices": {"source": prices.source, "date": prices.date},
        "files_scanned": data.files, "bad_lines": data.bad_lines,
        "totals": {"cost": round(total, 4), "requests": n, "sessions": len(sess), "active_days": len(days),
                   "cost_per_session": round(total / len(sess), 4) if sess else 0,
                   "cost_per_active_day": round(total / len(days), 4) if days else 0,
                   "cost_per_request": round(total / n, 5) if n else 0},
        "spend_by_type": split,
        "by_model": models,
        "context_buckets": buckets,
        "context": {"median": int(median(ctxs)), "p95": int(percentile(ctxs, 0.95)), "max": max(ctxs) if ctxs else 0,
                    "main_median": int(median(main_ctx)), "main_p95": int(percentile(main_ctx, 0.95)),
                    "share_over_300k": round(sum(b["share"] for b in buckets if b["bucket"] in ("300-600k", ">600k")), 2)},
        "cache_breaks": {
            "threshold": BREAK_MIN, "events": len(real), "tokens": sum(e["tokens"] for e in real),
            "cost": round(sum(e["cost"] for e in real), 4), "per_100_requests": round(100.0 * len(real) / n, 2) if n else 0,
            "big_appends": causes["big_append"][0] if "big_append" in causes else 0,
            "by_cause": [{"cause": c, "events": v[0], "tokens": v[1], "cost": round(v[2], 4)}
                         for c, v in sorted(causes.items(), key=lambda kv: -kv[1][2])],
            "top": [{"session": e["req"].session[:8], "at": iso(e["req"].ts), "sub": e["req"].sub,
                     "model": label(e["req"].model), "tokens": e["tokens"], "cost": round(e["cost"], 4),
                     "cause": e["cause"], "gap_min": e["gap_min"], "api_reason": label(e["req"].api_reason) if e["req"].api_reason else None}
                    for e in top_ev]},
        "subagents": {"cost": round(sub_cost, 4), "share": round(pct(sub_cost, total), 2),
                      "runs": len({r.thread for r in sub_reqs}), "requests": len(sub_reqs), "by_type_model": subs},
        "bash": {"calls": len(bash), "chars": bash_chars, "chars_per_call": int(bash_chars / len(bash)) if bash else 0,
                 "over_2k": sum(1 for b in bash if b.chars > 2000), "errors": sum(1 for b in bash if b.is_error),
                 "by_family": fams[:top]},
        "sessions": {"count": len(sess), "mean": round(total / len(sess), 4) if sess else 0,
                     "median": round(median(costs), 4), "p90": round(percentile(costs, 0.9), 4),
                     "max": round(max(costs), 4) if costs else 0,
                     "over_5": {"count": len(over5), "cost": round(sum(over5), 4), "share": round(pct(sum(over5), total), 2)},
                     "top": [{"session": k[:8], "project": label(proj_names.get(v["project"], v["project"])),
                              "started": iso(v["first"]), "requests": v["requests"], "cost": round(v["cost"], 4)}
                             for k, v in sorted(sess.items(), key=lambda kv: -kv[1]["cost"])[:top]]},
        "projects": projects[:top],
        "unpriced_models": {label(m): c for m, c in (unpriced or {}).items()},
    }
    report["squeeze_ledger"] = squeeze_realized(tsv_paths or [], data, window, prices, top)
    return report


# ---------------------------------------------------------------- text output

def table(headers, rows, indent="  "):
    cells = [[str(h) for h in headers]] + [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = []
    for row in cells:
        parts = [row[0].ljust(widths[0])] + [row[i].rjust(widths[i]) for i in range(1, len(row))]
        lines.append(indent + "  ".join(parts).rstrip())
    return lines


def render(rep):
    t = rep["totals"]
    w = rep["window"]
    span = "%s .. %s" % (w["since"] or rep["data_span"]["first"] or "-", w["until"] or rep["data_span"]["last"] or "-")
    out = ["hetz ledger  %s%s%s" % (span, "  project~" + w["project"] if w["project"] else "",
                                     "  session " + w["session"] if w["session"] else ""),
           "  %d requests, %d sessions, %d active days, %d files  |  prices: %s (%s)" % (
               t["requests"], t["sessions"], t["active_days"], rep["files_scanned"],
               "prices.json" if rep["prices"]["source"].endswith("prices.json") else rep["prices"]["source"],
               rep["prices"]["date"]),
           "  List-price estimate %s  |  %s/session  |  %s/active day  |  sessions > $5: %d (%s, %.0f%%)" % (
               fusd(t["cost"]), fusd(t["cost_per_session"]), fusd(t["cost_per_active_day"]),
               rep["sessions"]["over_5"]["count"], fusd(rep["sessions"]["over_5"]["cost"]), rep["sessions"]["over_5"]["share"]),
           ""]
    if not t["requests"]:
        out.append("  No requests in this window.")
    out.append("Spend by token type")
    out += table(["type", "tokens", "cost", "share"],
                 [[k, ftok(v["tokens"]), fusd(v["cost"]), "%.1f%%" % v["share"]]
                  for k, v in rep["spend_by_type"].items() if v["tokens"] or k != "web_search"])
    out += ["", "Cost by model"]
    out += table(["model", "requests", "cost", "share"],
                 [[m["model"], m["requests"], fusd(m["cost"]), "%.1f%%" % m["share"]] for m in rep["by_model"]])
    c = rep["context"]
    out += ["", "Cost by context size per request (median %s, p95 %s, max %s; share above 300k %.1f%%)" % (
        ftok(c["median"]), ftok(c["p95"]), ftok(c["max"]), c["share_over_300k"])]
    out += table(["bucket", "requests", "cost", "share"],
                 [[b["bucket"], b["requests"], fusd(b["cost"]), "%.1f%%" % b["share"]] for b in rep["context_buckets"]])
    cb = rep["cache_breaks"]
    out += ["", "Cache breaks (cache write > %s after a thread's first request): %d events, %s tokens, %s; %.1f per 100 requests" % (
        ftok(cb["threshold"]), cb["events"], ftok(cb["tokens"]), fusd(cb["cost"]), cb["per_100_requests"])]
    if cb["by_cause"]:
        out += table(["cause", "events", "tokens", "write cost"],
                     [[x["cause"] + (" (not a break)" if x["cause"] == "big_append" else ""), x["events"], ftok(x["tokens"]),
                       fusd(x["cost"])] for x in cb["by_cause"]])
        if cb["top"]:
            out += ["  largest:"]
            out += table(["session", "at", "model", "tokens", "cost", "cause", "gap"],
                         [[e["session"] + ("*" if e["sub"] else ""), e["at"], e["model"], ftok(e["tokens"]), fusd(e["cost"]),
                           e["cause"], "%sm" % e["gap_min"] if e["gap_min"] is not None else "-"] for e in cb["top"][:5]], "    ")
    sa = rep["subagents"]
    out += ["", "Subagents: %s (%.1f%% of spend), %d runs, %d requests" % (fusd(sa["cost"]), sa["share"], sa["runs"], sa["requests"])]
    if sa["by_type_model"]:
        out += table(["agent", "model", "runs", "reqs", "cost", "share", "cold start"],
                     [[s["agent"], s["model"], s["runs"], s["requests"], fusd(s["cost"]), "%.1f%%" % s["share"],
                       "%s tok" % ftok(s["cold_start_tokens_avg"])] for s in sa["by_type_model"]])
    b = rep["bash"]
    out += ["", "Bash results: %d calls, %s chars (%s/call), %d over 2k, %d errors" % (
        b["calls"], ftok(b["chars"]), ftok(b["chars_per_call"]), b["over_2k"], b["errors"])]
    if b["by_family"]:
        out += table(["family", "calls", "chars", "share", "avg", "max", ">2k"],
                     [[f["family"], f["calls"], ftok(f["chars"]), "%.1f%%" % f["share"], ftok(f["avg"]), ftok(f["max"]),
                       f["over_2k"]] for f in b["by_family"]])
    s = rep["sessions"]
    out += ["", "Sessions: %d, mean %s, median %s, p90 %s, max %s" % (
        s["count"], fusd(s["mean"]), fusd(s["median"]), fusd(s["p90"]), fusd(s["max"]))]
    if s["top"]:
        out += table(["session", "project", "started", "reqs", "cost"],
                     [[x["session"], x["project"], x["started"], x["requests"], fusd(x["cost"])] for x in s["top"][:5]])
    if len(rep["projects"]) > 1:
        out += ["", "Projects"]
        out += table(["project", "sessions", "cost", "share"],
                     [[p["project"], p["sessions"], fusd(p["cost"]), "%.1f%%" % p["share"]] for p in rep["projects"]])
    sq = rep.get("squeeze_ledger")
    if sq:
        out += ["", "Squeeze (live hook, squeeze.tsv): %d rows, %s of %s chars removed (%.1f%%), ~%s saved (est.)" % (
            sq["rows"], ftok(sq["chars_removed"]), ftok(sq["chars_in"]), sq["removed_pct"], fusd(sq["usd_saved_est"]))]
        if sq["unmatched_rows"]:
            out.append("  %d rows not found in transcripts: credited one cache write each (lower bound)" % sq["unmatched_rows"])
        if sq["by_family"]:
            out += table(["family", "calls", "chars in", "removed", "~saved"],
                         [[f["family"], f["calls"], ftok(f["chars_in"]), ftok(f["chars_removed"]), fusd(f["usd_saved_est"])]
                          for f in sq["by_family"]])
    if rep["unpriced_models"]:
        out += ["", "Unpriced models (cost counted as $0): " + ", ".join("%s (%d)" % kv for kv in rep["unpriced_models"].items())]
    if rep.get("replay"):
        out += [""] + render_replay(rep["replay"])
    out += ["", "List prices, not your bill; compare relative changes. Transcripts under-record output."]
    return "\n".join(out)


def render_replay(rp):
    out = ["Squeeze replay (%s): %d Bash results, %d errors skipped, %d squeezed, %d passed through, %d exceptions" % (
        os.path.relpath(rp["squeeze_path"], REPO) if rp["squeeze_path"].startswith(REPO) else rp["squeeze_path"],
        rp["results"], rp["errors_skipped"], rp["squeezed"], rp["passthrough"], rp["exceptions"]),
        "  chars %s -> %s, saved %s (%.1f%% of Bash result chars) over %.1f days" % (
            ftok(rp["chars_in"]), ftok(rp["chars_out"]), ftok(rp["chars_saved"]), rp["saved_pct"], rp["span_days"]),
        "  KEEP lines lost: %d%s" % (rp["keep_lost"], "  <-- MUST BE 0" if rp["keep_lost"] else ""),
        "  default-on gate (>= %d%% saved, 0 KEEP lost, >= %d days): %s" % (
            GATE_PCT, GATE_DAYS, "PASS" if rp["gate"]["pass"] else "FAIL (" + ", ".join(rp["gate"]["reasons"]) + ")")]
    if rp["by_family"]:
        out += table(["family", "calls", "squeezed", "chars in", "saved", "saved%", "keep lost", "exc"],
                     [[f["family"], f["calls"], f["squeezed"], ftok(f["chars_in"]), ftok(f["chars_saved"]),
                       "%.1f%%" % f["saved_pct"], f["keep_lost"], f["exceptions"]] for f in rp["by_family"]])
    if rp["keep_lost_events"]:
        out += ["  KEEP losses (look up by id; content not shown):"]
        out += table(["session", "tool_use_id", "family", "lines"],
                     [[e["session"], e["tool_use_id"], e["family"], e["lines"]] for e in rp["keep_lost_events"][:10]], "    ")
    return out


COMPARE_METRICS = (
    ("cost", lambda r: r["totals"]["cost"], "usd"),
    ("requests", lambda r: r["totals"]["requests"], "int"),
    ("sessions", lambda r: r["totals"]["sessions"], "int"),
    ("active days", lambda r: r["totals"]["active_days"], "int"),
    ("$/active day", lambda r: r["totals"]["cost_per_active_day"], "usd"),
    ("$/session", lambda r: r["totals"]["cost_per_session"], "usd"),
    ("$/request", lambda r: r["totals"]["cost_per_request"], "usd4"),
    ("cache read share", lambda r: r["spend_by_type"]["cache_read"]["share"], "pct"),
    ("cache write share", lambda r: r["spend_by_type"]["cache_write_5m"]["share"] + r["spend_by_type"]["cache_write_1h"]["share"], "pct"),
    ("output share", lambda r: r["spend_by_type"]["output"]["share"], "pct"),
    ("cost share >300k ctx", lambda r: r["context"]["share_over_300k"], "pct"),
    ("median context", lambda r: r["context"]["median"], "tok"),
    ("p95 context", lambda r: r["context"]["p95"], "tok"),
    ("cache breaks", lambda r: r["cache_breaks"]["events"], "int"),
    ("breaks /100 req", lambda r: r["cache_breaks"]["per_100_requests"], "num"),
    ("bash chars/call", lambda r: r["bash"]["chars_per_call"], "tok"),
    ("subagent share", lambda r: r["subagents"]["share"], "pct"),
    ("sessions > $5", lambda r: r["sessions"]["over_5"]["count"], "int"),
)


def compare(rep_a, rep_b):
    rows = []
    for name, fn, kind in COMPARE_METRICS:
        a, b = fn(rep_a), fn(rep_b)
        rows.append({"metric": name, "a": a, "b": b, "delta_pct": round(pct(b - a, a), 1) if a else None, "kind": kind})
    return rows


def render_compare(ra, rb, rows, labels):
    def fmt(v, kind):
        return {"usd": lambda: fusd(v), "usd4": lambda: "$%.4f" % v, "pct": lambda: "%.1f%%" % v,
                "tok": lambda: ftok(v), "int": lambda: str(int(v)), "num": lambda: "%.2f" % v}[kind]()
    out = ["hetz ledger compare   A = %s   B = %s" % labels, ""]
    out += table(["metric", "A", "B", "delta"],
                 [[r["metric"], fmt(r["a"], r["kind"]), fmt(r["b"], r["kind"]),
                   ("%+.1f%%" % r["delta_pct"]) if r["delta_pct"] is not None else "-"] for r in rows])
    out += ["", "Normalize by active day/session/request: the work differs between windows (confirmation, not proof)."]
    return "\n".join(out)


# ---------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(prog="hetz_ledger.py", formatter_class=argparse.RawDescriptionHelpFormatter,
                                description=__doc__.split("\n\n")[0],
                                epilog="Reads transcripts read-only; prints aggregates and command family names only.")
    p.add_argument("--since", help="window start: 12h, 14d, 2w or YYYY-MM-DD (default: everything)")
    p.add_argument("--until", help="window end (exclusive), same formats; a date includes that whole day")
    p.add_argument("--project", help="project name fragment (cwd basename or ~/.claude/projects dir) or a path")
    p.add_argument("--session", help="session id or id prefix")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--compare", nargs=2, metavar=("A..B", "C..D"),
                   help="compare two windows, e.g. 2026-09-01..2026-09-30 2026-10-01..2026-10-17 or 30d..14d 14d..")
    p.add_argument("--replay-squeeze", action="store_true",
                   help="run historical Bash results through squeeze.py offline; exit 3 if a KEEP line would be lost")
    p.add_argument("--top", type=int, default=10, help="rows per table (default 10)")
    p.add_argument("--projects-dir", help="transcript root (default $CLAUDE_CONFIG_DIR/projects or ~/.claude/projects)")
    p.add_argument("--prices", help="prices.json path (default plugins/hetz/hooks/prices.json, else built-in table)")
    p.add_argument("--squeeze", dest="squeeze_path", help="squeeze.py for --replay-squeeze (default plugins/hetz/hooks/squeeze.py)")
    p.add_argument("--squeeze-tsv", help="live squeeze ledger (default: found in the plugin data dir)")
    return p


def default_projects_dir():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(cfg, "projects")


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.compare and (args.replay_squeeze or args.since or args.until):
        sys.stderr.write("hetz_ledger: --compare sets its own windows; it cannot be combined with "
                         "--replay-squeeze, --since or --until\n")
        return 2
    root =os.path.expanduser(args.projects_dir or default_projects_dir())
    if not os.path.isdir(root):
        sys.stderr.write("hetz_ledger: no transcripts at %s\n" % root)
        return 2
    try:
        now = time_now()
        project = project_needle(args.project)
        if args.compare:
            ranges = [parse_range(x, now) for x in args.compare]
            windows = [Window(a, b, project, args.session) for a, b in ranges]
        else:
            windows = [Window(parse_when(args.since, now=now), parse_when(args.until, end=True, now=now), project, args.session)]
    except ValueError as e:
        sys.stderr.write("hetz_ledger: %s\n" % e)
        return 2
    prices = Prices(args.prices)
    starts = [w.start for w in windows]
    min_mtime = None if any(s is None for s in starts) else min(starts)

    replay = None
    saved_env = {k: os.environ.get(k) for k in ("CLAUDE_PLUGIN_DATA", "HETZ_SQUEEZE")}
    scratch = tempfile.mkdtemp(prefix="hetz-replay-") if args.replay_squeeze else None
    try:
        if scratch:
            os.environ["CLAUDE_PLUGIN_DATA"] = scratch    # any side-writes land in a throwaway dir
            os.environ.pop("HETZ_SQUEEZE", None)          # replay the filter even if the live switch is off
            path = os.path.abspath(os.path.expanduser(args.squeeze_path or DEFAULT_SQUEEZE))
            try:
                replay = Replay(path)
            except (Exception, SystemExit) as e:          # a broken squeeze.py must not crash the ledger
                sys.stderr.write("hetz_ledger: cannot load squeeze from %s: %s\n" % (path, type(e).__name__))
                return 2
        data = scan(root, min_mtime=min_mtime, replay=replay, window=windows[0])
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    unpriced = price_all(data, prices)
    breaks = find_breaks(data)
    tsv = squeeze_tsv_paths(args.squeeze_tsv)
    reports = [build_report(data, prices, w, args.top, unpriced, breaks, tsv) for w in windows]

    if args.compare:
        rows = compare(reports[0], reports[1])
        if args.json:
            print(json.dumps({"ranges": args.compare, "a": reports[0], "b": reports[1],
                              "delta": [{k: v for k, v in r.items() if k != "kind"} for r in rows]}, indent=1))
        else:
            print(render_compare(reports[0], reports[1], rows, tuple(args.compare)))
        return 0

    rep = reports[0]
    if replay is not None:
        rep["replay"] = replay.summary(rep["bash"]["chars"], args.top)
    print(json.dumps(rep, indent=1) if args.json else render(rep))
    if replay is not None and rep["replay"]["keep_lost"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
