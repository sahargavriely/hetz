#!/usr/bin/env python3
"""hetz squeeze: PostToolUse(Bash) hook that shrinks noisy output of allowlisted install/build/test
commands before it enters context. It never rewrites commands, never drops KEEP (error-like) lines,
always saves the raw output and shows its path. Fails open: any problem -> no stdout, exit 0.

Behaviour of Claude Code 2.1.272 this relies on (checked in the binary):
- tool_response is the Bash output object {stdout, stderr, interrupted, isImage?, noOutputExpected?,
  returnCodeInterpretation?, backgroundTaskId?, persistedOutputPath?, persistedOutputSize?, ...}.
  The shell merges stderr into stdout; tool_response.stderr only carries harness notes.
- updatedToolOutput must pass the same zod schema (stdout/stderr strings, interrupted bool) or it is
  dropped with a hook error; the model then sees stdout + "\\n" + stderr.
- PostToolUse fires only for non-error exits (non-zero -> PostToolUseFailure; grep/rg/find/diff/test
  exit 1 counts as success). So failing runs reach the model untouched.
- Output over bashOutputMaxChars / BASH_MAX_OUTPUT_LENGTH (default 30000) arrives head-capped, with
  persistedOutputPath holding the full text; the model would get only a ~2 KB preview. We squeeze the
  full file and inline the result only when it is no bigger than that preview (net-negative), which
  also spares a follow-up read of the file.

Public contract: squeeze(command, stdout, stderr) -> dict | None (pure; used by the offline replay)
and KEEP_RE. Every line KEEP_RE matches in the raw output (split like str.splitlines) appears verbatim,
ANSI codes and surrounding whitespace aside, in the squeezed output.
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hetzlib  # noqa: E402

MIN_CHARS = 2000            # smaller outputs pass through
MIN_SAVING = 0.30           # rewrite only if at least this share of chars goes (footer counted)
FOOTER_EST = 220
STAGE2_LINES, HEAD, TAIL = 80, 30, 40
TIGHT_HEAD, TIGHT_TAIL = 8, 20   # stage 2 retry for an over-cap output that must fit the preview budget
RUN_MIN, RUN_KEEP = 6, 2    # >5 consecutive lines equal up to digits/hashes -> first 2 + marker
FULL_READ_MAX = 2_000_000   # bytes of an over-cap (persisted) output we re-read
PREVIEW_CHARS = 2300        # what the model sees for an over-cap output (2000-char preview + header)
LONG_LINE = 400             # longer lines skip the success/deprecation exclusion (they stay KEEP)
LOG_TTL_DAYS = 14
TIPS_TSV = os.path.join(os.path.dirname(hetzlib.HOOKS_DIR), "share", "quiet-flags.tsv")

# ---------------------------------------------------------------- KEEP: never dropped
_KEEP_WORDS = (r"error|err!|err_|fail|warn|exception|traceback|panic|fatal|denied|cannot|can't|not found|"
               r"no such file|assert|expected|exit code|exit status|abort|segmentation fault|"
               r"undefined reference|unresolved|conflict|rejected|vulnerab|timed out|killed|✗|✕|✘")
# Lines that contain a KEEP word but only report success or a package-manager deprecation.
# Every alternative is linear-time (no nested ambiguous repeats): it runs on lines of any length.
_BENIGN = (r"(?-i:\s*PASS\s|\s*[✓✔√]\s|(?=\S+::\S)\S+\s(?:.*\s)?PASSED\b|\s*--- PASS: "
           r"|=== (?:RUN|PAUSE|CONT|NAME)\s|ok\s+\S+\s+(?:\(cached\)|[\d.]+s)"
           r"|\s{0,12}(?:Compiling|Checking|Downloaded|Fresh|Documenting) [\w-]+ v\d\S*(?: \([^)]*\))?\s*$"
           r"|\s*(?:Collecting|Downloading|Using cached) \S+(?: \([^)]*\))*\s*$)"
           r"|\s*npm\s+warn\s+deprecated\s|\s*warn\s+(?:\d+\s+)?deprecated\s"
           r"|warning\s+(?:[^\s>]+\s+>\s+)*(?=[^\s:]*@)[^\s:]+:.*\bdeprecated\b")
KEEP_RE = re.compile(r"^(?!(?=.{0,%d}$)(?:%s)).*?(?:%s)" % (LONG_LINE, _BENIGN, _KEEP_WORDS), re.I)
_KEEP_LOW = re.compile(_KEEP_WORDS)        # searched on line.lower(): ~7x faster than re.I
_BENIGN_RE = re.compile(r"(?:%s)" % _BENIGN, re.I)


def _is_keep(line):
    """Same answer as KEEP_RE.search(line), faster."""
    return bool(_KEEP_LOW.search(line.lower())) and not (len(line) <= LONG_LINE and _BENIGN_RE.match(line))


# ---------------------------------------------------------------- noise: dropped unless protected
NOISE_RE = re.compile("|".join("(?:%s)" % p for p in (
    # npm / pnpm / yarn / bun
    r"npm (?:notice|WARN deprecated|warn deprecated)\b", r"\s*WARN\s+(?:\d+\s+)?deprecated\s",
    r"warning\s+(?:[^\s>]+\s+>\s+)*(?=[^\s:]*@)[^\s:]+:.*\bdeprecated\b",
    r"\d+ packages? (?:is|are) looking for funding",
    r"\s+run `npm fund` for details", r"Progress: resolved \d+",
    # pip / uv
    r"\s*(?:Collecting|Requirement already satisfied|Using cached|Obtaining|Looking in indexes|"
    r"Preparing metadata|Getting requirements to build|Installing build dependencies|Building wheels? for"
    r"|Created wheel for|Stored in directory|Attempting uninstall|Found existing installation|Uninstalling"
    r"|Successfully uninstalled|Installing collected packages)\b",
    r"\s*Downloading\s", r"\s[+-] [\w.\-\[\],]+==\S+\s*$",
    # cargo
    r"\s{0,12}(?:Compiling|Checking|Downloaded|Fresh|Updating|Locking|Adding|Documenting|Blocking|Unpacking) \S",
    # docker (BuildKit plain progress, pull/push, legacy builder)
    r"#\d+ (?:sha256:|extracting |resolve |DONE\b|CACHED\b|transferring |\[internal\]|exporting layers"
    r"|writing image|unpacking to|preparing layers|exporting (?:manifest|config|attestation))",
    r"[0-9a-f]{12}: (?:Pulling fs layer|Waiting|Downloading|Verifying Checksum|Download complete|Extracting"
    r"|Pull complete|Already exists|Pushing|Pushed|Layer already exists|Preparing|Mounted from)",
    r"\s*---> (?:Running in|Using cache|[0-9a-f]{12}\s*$)", r"Removing intermediate container",
    # gradle / maven
    r"> Task \S+ (?:UP-TO-DATE|NO-SOURCE|SKIPPED|FROM-CACHE)\s*$", r"\[INFO\] Download(?:ing|ed)(?: from)?[ :]",
    r"Progress \(\d+\): ",
    # jest / vitest / pytest / go test (passes only; failures are KEEP)
    r"\s*PASS\s+\S", r"\s*[✓✔√]\s", r"(?:\S+\.py\s+[.sx]+|[.sx]{3,})\s*(?:\[\s*\d+%\])?\s*$",
    r"(?=\S+::\S)\S+\s(?:.*\s)?PASSED\b", r"\s*--- PASS: ", r"=== (?:RUN|PAUSE|CONT|NAME)\s",
    r"go: (?:downloading|finding|extracting) ",
    # make / cmake / brew / apt / terraform / bundler / composer
    r"make(?:\[\d+\])?: (?:Entering|Leaving) directory", r"\[\s*\d+%\] (?:Building|Scanning dependencies|Generating)",
    r"==> (?:Downloading|Fetching|Pouring|Verifying)", r"Already downloaded: ",
    r"(?:Get|Hit|Ign):\d+ ", r"(?:Reading package lists|Building dependency tree|Reading state information)",
    r"Selecting previously unselected package", r"Preparing to unpack", r"Unpacking \S+ \(", r"Setting up \S+ \(",
    r"Processing triggers for ", r"\(Reading database",
    r"- (?:Finding|Installing|Installed|Using previously-installed|Reusing previous version) ",
    r"(?:Fetching|Using|Installing) [\w.-]+ \d[\w.]*(?: \(was [\w.]+\))?(?: with native extensions)?\s*$",
    r"\s+- (?:Downloading|Installing|Locking|Upgrading) ",
)))
_PASS_HEAD_RE = re.compile(r"\s*PASS\s+\S")
_BK_PREFIX = re.compile(r"#\d+ \d+(?:\.\d+)? ")     # BuildKit step-output timestamp prefix
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_BAR_RE = re.compile(r"[#=█▉▊▋▌▍▎▏━░▒▓■▰▱]{4,}")
_PCT_RE = re.compile(r"\d(?:\.\d+)?\s?%")
_SIZE_RE = re.compile(r"\d(?:\.\d+)?\s?[kKMG]i?B?\s?/\s?\d|\d\s?/\s?\d+(?:\.\d+)?\s?[kKMG]i?B")
_PROGRESS_ONLY_RE = re.compile(r"\s*(?:\d{1,3}(?:\.\d+)?\s?%|[|/\\-])\s*$")
_NUM_RE = re.compile(r"[0-9a-fA-F]{7,}|\d+")


def _is_progress(line):
    s = line.lstrip()
    if s[:1] and "\u2800" <= s[0] <= "\u28ff":          # braille spinner frame
        return True
    if len(line) > 400:
        return False
    if _PROGRESS_ONLY_RE.match(line):
        return True
    return bool(_BAR_RE.search(line)) and bool(_PCT_RE.search(line) or _SIZE_RE.search(line))


def _is_noise(line):
    if NOISE_RE.match(line):
        return True
    m = _BK_PREFIX.match(line)
    return bool(m) and bool(NOISE_RE.match(line, m.end()))


# ---------------------------------------------------------------- command classification
CONTENT = {"cat", "head", "tail", "sed", "awk", "less", "more", "bat", "nl", "jq", "yq", "grep", "egrep",
           "fgrep", "rg", "ag", "find", "fd", "diff", "wc", "echo", "printf", "rtk"}
NEUTRAL = {"cd", "pushd", "popd", "export", "set", "unset", "source", ".", "true", ":", "mkdir", "touch",
           "sleep", "shopt", "umask", "ulimit", "trap", "wait", "echo", "printf", "rm", "cp", "mv", "ln",
           "chmod", "nvm", "conda", "deactivate", "corepack", "for", "done", "fi", "}", ")"}
GIT_CONTENT = {"diff", "show", "blame", "cat-file", "grep", "format-patch", "range-diff"}
ANY = None
_JS_SUBS = {"install", "i", "ci", "add", "build", "test", "t", "run", "run-script", "rebuild", "update", "up",
            "upgrade", "install-test", "it", "lint", "typecheck", "type-check", "check", "compile"}
NOISY = {
    "npm": _JS_SUBS, "pnpm": _JS_SUBS, "bun": _JS_SUBS, "yarn": _JS_SUBS | {""},
    "pip": {"install"}, "pip3": {"install"}, "uv": {"sync", "add", "lock", "pip"},
    "poetry": {"install", "add", "update", "lock", "sync"},
    "brew": {"install", "reinstall", "upgrade", "update", "bundle", "fetch"},
    "apt": {"install", "update", "upgrade", "full-upgrade", "dist-upgrade"},
    "apt-get": {"install", "update", "upgrade", "dist-upgrade"},
    "docker": {"build", "pull", "push", "compose", "buildx"},
    "docker-compose": {"build", "pull", "push", "create"},
    "cargo": {"build", "b", "check", "c", "test", "t", "clippy", "install", "fetch", "update", "doc", "bench"},
    "go": {"build", "test", "mod", "get", "install"},
    "make": ANY, "gmake": ANY, "cmake": ANY, "ninja": ANY, "gradle": ANY, "gradlew": ANY, "mvn": ANY, "mvnw": ANY,
    "pytest": ANY, "py.test": ANY, "jest": ANY, "vitest": ANY, "tsc": ANY, "eslint": ANY, "mocha": ANY,
    "terraform": {"init"}, "bundle": {"", "install", "update"}, "composer": {"install", "update", "require"},
}
_SUB2 = {("uv", "pip"): {"install", "sync"}, ("docker", "compose"): {"build", "pull", "push", "create"},
         ("docker", "buildx"): {"build", "bake"}, ("go", "mod"): {"tidy", "download", "vendor", "verify"}}

# A noisy tool asked for a listing or structured data prints the content the model wants: pass through.
_CONTENT_FLAGS = frozenset((
    "--collect-only", "--co", "--fixtures", "--fixtures-per-test", "--markers", "--setup-plan", "--setup-only",
    "--setup-show", "-list", "--showConfig", "--show-config", "--print-config", "--explainFiles", "--diagnostics",
    "--extendedDiagnostics", "--json", "-json", "--parseable", "--dry-run", "--just-print", "--recon",
    "--print-data-base", "--simulate", "--no-act", "--report", "--build-plan", "--unit-graph", "-h",
    "--system-information", "--find-package", "--debug"))
_CONTENT_PREFIXES = ("--list", "--trace", "--help")      # --listTests, --list-presets, --traceResolution, --help-full
_FORMAT_FLAGS = frozenset(("--format", "--reporter", "--reporters", "--message-format", "--output-format",
                           "--progress", "--call", "--print"))
_FORMAT_VAL_RE = re.compile(r"json|xml|junit|tap|sarif|checkstyle|outline|targets", re.I)
_TOOL_CONTENT_FLAGS = {"make": {"-p"}, "gmake": {"-p"}, "ninja": {"-n", "-t"}, "go": {"-n"},
                       "gradle": {"-m"}, "gradlew": {"-m"}, "cmake": {"-E", "-P", "-N"}, "apt": {"-s"},
                       "apt-get": {"-s"}, "brew": {"-n"}, "eslint": {"-f"}}
_MAKE_DRY_RE = re.compile(r"-[BdeikqrRsStvw]*[np][BdeiknpqrRsStvw]*$")   # -n, -p, -np, -rpn (no-arg clusters)
_CMAKE_LIST_RE = re.compile(r"-L[AH]*$")
_TARGET_RE = re.compile(r"(?:help|list|ls|targets|logs?|status|ps|env|vars|printvars|info|show|dump|routes|urls"
                        r"|tree|graph)$|(?:print|show|list|dump|logs?|routes)[-_:]")
_GRADLE_TASKS = frozenset(("dependencies", "dependencyInsight", "tasks", "properties", "projects", "help",
                           "buildEnvironment", "outgoingVariants", "resolvableConfigurations", "javaToolchains",
                           "components", "model", "dependentComponents"))
_MVN_GOAL_RE = re.compile(r"(?:dependency:(?!go-offline|copy|unpack|purge|resolve-plugins)|help:|versions:display)"
                          r"|\S*maven-(?:dependency|help)-plugin:")
_WRAPPERS = {"(", "{", "!", "time", "command", "exec", "nice", "nohup", "sudo", "env", "builtin", "npx", "bunx",
             "pnpx", "if", "elif", "then", "else", "while", "until", "do"}
_RUNNERS = {"uv": "run", "poetry": "run", "pipenv": "run", "pdm": "run", "hatch": "run", "rye": "run",
            "pnpm": "exec", "yarn": "exec", "bundle": "exec"}
_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PY_RE = re.compile(r"python(?:\d+(?:\.\d+)?)?$")
_REDIR_RE = re.compile(r"\d?[<>]")


def _segments(cmd):
    """Quote-aware split into simple commands: [(words, piped)], piped = later stage of a pipeline."""
    segs, words, cur = [], [], []
    has, piped, quote = False, False, None
    i, n = 0, len(cmd)

    def flush():
        nonlocal has
        if has:
            words.append("".join(cur))
        cur.clear()
        has = False

    while i < n:
        c = cmd[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < n:
                i += 1
                cur.append(cmd[i])
            else:
                cur.append(c)
            i += 1
            continue
        if c in "'\"":
            quote, has = c, True
        elif c == "\\" and i + 1 < n:
            cur.append(cmd[i + 1])
            has = True
            i += 1
        elif c in " \t":
            flush()
        elif c == "#" and not has:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
            continue
        elif c == "$" and cmd[i + 1:i + 2] == "(":
            depth, j = 0, i + 1
            while j < n:
                depth += (cmd[j] == "(") - (cmd[j] == ")")
                if depth == 0:
                    break
                j += 1
            cur.append(cmd[i:j + 1])
            has = True
            i = j
        elif c == "&" and ((cur and cur[-1] in "<>") or cmd[i + 1:i + 2] == ">"):
            cur.append(c)
            has = True
        elif c in ";\n&|()":
            flush()
            two = cmd[i:i + 2]
            op = two if two in ("&&", "||", "|&") else c
            i += len(op)
            if words:
                segs.append((list(words), piped))
            words.clear()
            piped = op in ("|", "|&")
            continue
        else:
            cur.append(c)
            has = True
        i += 1
    flush()
    if words:
        segs.append((list(words), piped))
    return segs


def _strip_wrappers(words):
    """Drop env assignments and wrappers (time, npx, uv run, python -m, ...) in front of the real tool."""
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        if _ASSIGN_RE.match(w) or w in _WRAPPERS:
            i += 1
            while w in ("env", "sudo", "nice", "npx", "bunx", "pnpx") and i < n and words[i].startswith("-"):
                i += 1
            continue
        if w == "timeout":
            i += 1
            while i < n and words[i].startswith("-"):
                i += 1
            i += 1                       # the duration
            continue
        if _RUNNERS.get(w) and i + 1 < n and words[i + 1] == _RUNNERS[w]:
            i += 2
            continue
        if _PY_RE.match(os.path.basename(w)) and i + 2 < n and words[i + 1] == "-m":
            return words[i + 2:]
        break
    return words[i:]


def _subcommand(words, start=1):
    for k in range(start, len(words)):
        if not words[k].startswith("-"):
            return k, words[k]
    return len(words), ""


def _content_call(name, words, sub, k):
    """True when this call of a noisy tool prints a listing or structured data (pytest --collect-only,
    go test -json, gradle dependencies, make -n, npm run with no script, make help, ...)."""
    extra = _TOOL_CONTENT_FLAGS.get(name, ())
    make = name in ("make", "gmake")
    for i in range(1, len(words)):
        w = words[i]
        flag, eq, val = w.partition("=")
        if flag in _CONTENT_FLAGS or w in extra or flag.startswith(_CONTENT_PREFIXES):
            return True
        if flag in _FORMAT_FLAGS or (flag == "-f" and name == "eslint"):
            if _FORMAT_VAL_RE.search(val if eq else (words[i + 1] if i + 1 < len(words) else "")):
                return True
        plain = not w.startswith("-")
        if make and (_MAKE_DRY_RE.match(w) or (plain and not eq and _TARGET_RE.match(w))):
            return True
        if (name == "cmake" and _CMAKE_LIST_RE.match(w)) \
                or (name in ("gradle", "gradlew") and plain and w.rsplit(":", 1)[-1] in _GRADLE_TASKS) \
                or (name in ("mvn", "mvnw") and _MVN_GOAL_RE.match(w)):
            return True
    if name == "vitest":
        return _subcommand(words)[1] == "list"
    if name in ("npm", "pnpm", "yarn", "bun") and sub in ("run", "run-script"):
        script = _subcommand(words, k + 1)[1]
        return not script or bool(_TARGET_RE.match(script))     # bare `npm run` lists the scripts
    return False


def _classify(words):
    """('noisy' | 'neutral' | 'content' | 'other', tool key, subcommand) for one simple command."""
    words = _strip_wrappers(words)
    words = [w for w in words if not _REDIR_RE.match(w)] or words
    if not words:
        return "neutral", "", ""
    name = os.path.basename(words[0])
    if name == "git":
        k = 1
        while k < len(words) and words[k].startswith("-"):
            k += 2 if words[k] in ("-C", "-c") else 1
        sub = words[k] if k < len(words) else ""
        return ("content" if sub in GIT_CONTENT or (sub == "log" and ("-p" in words or "--patch" in words))
                else "other"), "git", sub
    if name in NOISY:
        subs = NOISY[name]
        k, sub = (0, "") if subs is ANY else _subcommand(words)
        if subs is not ANY:
            sub2 = _SUB2.get((name, sub))
            if sub not in subs or (sub2 is not None and _subcommand(words, k + 1)[1] not in sub2):
                return "other", name, sub
        return ("content" if _content_call(name, words, sub, k) else "noisy"), name, sub
    if name in NEUTRAL:
        return "neutral", name, ""
    return ("content" if name in CONTENT else "other"), name, ""


def _tool_info(command):
    """(tool key, subcommand) when every producer in the command is noisy or neutral, else (None, '')."""
    if not isinstance(command, str) or re.search(r"#\s*hz:raw", command):
        return None, ""
    key, sub, first = None, "", True
    for words, piped in _segments(command):
        if piped:
            continue                     # tail/grep/head filtering a producer is fine
        kind, name, s = _classify(words)
        if first and name in ("echo", "printf"):
            return None, ""
        first = False
        if kind in ("content", "other"):
            return None, ""
        if kind == "noisy" and key is None:
            key, sub = name, s
    return key, sub


def tool_key(command):
    """Tool key (e.g. 'npm', 'pytest') when every producer in the command is noisy or neutral, else None."""
    return _tool_info(command)[0]


# ---------------------------------------------------------------- pipeline
def _clean_lines(text):
    """[(line, keep)]: ANSI/OSC stripped, a \\r-overwritten line reduced to its last segment. KEEP is judged on
    the raw and the clean text, and an overwritten KEEP segment stays as its own line, so every line KEEP_RE
    matches in the raw text (split like str.splitlines) survives with its text intact."""
    out = []
    for raw in text.split("\n"):
        segs = [s for s in raw.split("\r") if s] if "\r" in raw else [raw]
        last = len(segs) - 1
        for idx, seg in enumerate(segs or [""]):
            clean = _ANSI_RE.sub("", seg) if "\x1b" in seg else seg
            keep = _is_keep(clean) or (clean is not seg and _is_keep(seg))
            if keep or idx >= last:
                out.append((clean, keep))
    return out


def _norm(line):
    return _NUM_RE.sub("#", line)


def _filter(text, head=HEAD, tail=TAIL, cap=STAGE2_LINES):
    """Stage 1 + stage 2 on one stream. Returns the list of output lines."""
    lines = []
    for ln, keep in _clean_lines(text):
        if keep or not _is_progress(ln):
            lines.append((ln, keep))
    n = len(lines)
    prot = [k for _, k in lines]
    for i, (_, k) in enumerate(lines):
        if k:
            if i:
                prot[i - 1] = True
            if i + 1 < n:
                prot[i + 1] = True
    items, pass_ind = [], None           # pass_ind: inside a jest "PASS file" block (indented lines)
    for (ln, k), p in zip(lines, prot):
        body = ln.lstrip()
        ind = len(ln) - len(body)
        if pass_ind is not None and (not body or ind <= pass_ind or k):
            pass_ind = None
        if _PASS_HEAD_RE.match(ln):
            pass_ind = ind
        if p or not (pass_ind is not None or _is_noise(ln)):
            items.append((ln, p))

    # identical runs (>=3) -> one line + (xN); blank runs -> one blank. A protected line keeps its exact
    # text: it is followed by a separate marker instead.
    out, i, n = [], 0, len(items)
    while i < n:
        j = i + 1
        while j < n and items[j][0] == items[i][0]:
            j += 1
        ln, p = items[i][0], any(q for _, q in items[i:j])
        if not ln.strip():
            out.append((ln, p))
        elif j - i < 3:
            out.extend(items[i:j])
        elif p:
            out += [(ln, True), ("… repeated %d more times" % (j - i - 1), True)]
        else:
            out.append(("%s (×%d)" % (ln, j - i), False))
        i = j

    # similar runs (>5 unprotected lines equal up to digits/hashes) -> first 2 + marker. Distinct lines that
    # merely share a prefix (paths, test ids, package coordinates, coverage rows) are content: kept.
    items, out, i, n = out, [], 0, len(out)
    keys = [_norm(ln) for ln, _ in items]
    while i < n:
        if items[i][1]:
            out.append(items[i])
            i += 1
            continue
        j = i + 1
        while j < n and not items[j][1] and keys[j] == keys[i]:
            j += 1
        if j - i >= RUN_MIN and keys[i].strip():
            out.extend(items[i:i + RUN_KEEP])
            out.append(("… %d similar lines" % (j - i - RUN_KEEP), False))
        else:
            out.extend(items[i:j])
        i = j

    # stage 2: first `head` + protected (KEEP +-1) + last `tail`
    if len(out) > cap:
        n, res, gap = len(out), [], 0
        for i, (ln, p) in enumerate(out):
            if i < head or i >= n - tail or p:
                if gap:
                    res.append(("… %d lines omitted …" % gap, False))
                    gap = 0
                res.append((ln, p))
            else:
                gap += 1
        out = res
    return [ln for ln, _ in out]


def _count(text):
    text = text.rstrip("\n")
    return text.count("\n") + 1 if text else 0


def squeeze(command, stdout, stderr, tight=False):
    """Squeezed {stdout, stderr, lines_in, lines_out, chars_in, chars_out, tip} or None (pass through).
    Pure: no I/O besides reading share/quiet-flags.tsv for the tip. chars_out excludes the footer.
    tight=True shortens stage 2 (first 8 + KEEP lines + last 20) for outputs that must fit a small budget."""
    try:
        if not isinstance(stdout, str):
            return None
        stderr = stderr if isinstance(stderr, str) else ""
        chars_in = len(stdout) + len(stderr)
        if chars_in < MIN_CHARS:
            return None
        key, sub = _tool_info(command)
        if key is None:
            return None
        stage2 = (TIGHT_HEAD, TIGHT_TAIL, TIGHT_HEAD + TIGHT_TAIL) if tight else (HEAD, TAIL, STAGE2_LINES)
        new_out = "\n".join(_filter(stdout, *stage2)).strip("\n")
        new_err = stderr if len(stderr) < MIN_CHARS else "\n".join(_filter(stderr, *stage2)).strip("\n")
        chars_out = len(new_out) + len(new_err)
        if chars_out + FOOTER_EST > (1 - MIN_SAVING) * chars_in:
            return None
        return {"stdout": new_out, "stderr": new_err,
                "lines_in": _count(stdout) + _count(stderr), "lines_out": _count(new_out) + _count(new_err),
                "chars_in": chars_in, "chars_out": chars_out, "tip": tip_for(key, command, sub)}
    except Exception:
        return None


# ---------------------------------------------------------------- tips
_TIPS = None


def _load_tips():
    """{'tool' or 'tool subcommand': (quieter invocation, caveat)} from share/quiet-flags.tsv."""
    global _TIPS
    if _TIPS is None:
        _TIPS = {}
        try:
            with open(TIPS_TSV, encoding="utf-8") as f:
                for row in f:
                    if row.strip() and not row.startswith("#"):
                        cols = row.rstrip("\n").split("\t")
                        if len(cols) >= 2:
                            _TIPS[" ".join(cols[0].split())] = (cols[1], cols[2] if len(cols) > 2 else "")
        except OSError:
            pass
    return _TIPS


def tip_for(key, command, sub=""):
    """Footer tip for a squeezed command: row 'key sub', else 'key', else 'generic'; '' if already quiet."""
    tips = _load_tips()
    tip, note = (sub and tips.get("%s %s" % (key, sub))) or tips.get(key) or tips.get("generic") or ("", "")
    if not tip:
        return ""
    flags = [w for w in tip.split() if w.startswith("-")]
    if (flags and all(f in command for f in flags)) or (not flags and tip in " ".join(command.split())):
        return ""                        # already quiet
    return "%s (%s)" % (tip, note) if note else tip


# ---------------------------------------------------------------- hook side effects
_SAFE_RE = re.compile(r"[A-Za-z0-9._-]{1,128}$")


def _safe(value):
    return value if isinstance(value, str) and _SAFE_RE.match(value) and value not in (".", "..") else ""


def _read_text(path, limit):
    try:
        if not isinstance(path, str) or os.path.getsize(path) > limit:
            return None
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return None


def _prune_logs(root):
    try:
        import shutil
        cutoff = time.time() - LOG_TTL_DAYS * 86400
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(p) and os.path.getmtime(p) < cutoff:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _write_raw(path, command, stdout, stderr):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("$ %s\n%s\n" % (command, stdout))
            if stderr:
                f.write("[stderr]\n%s\n" % stderr)
        return True
    except OSError:
        return False


def _ledger(sid, command, chars_in, chars_out):
    try:
        head = " ".join(command.split())[:80]
        row = "%s\t%s\t%s\t%d\t%d\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), sid, head,
                                        chars_in, chars_out)
        with open(os.path.join(hetzlib.data_dir(), "squeeze.tsv"), "a", encoding="utf-8") as f:
            f.write(row)
    except OSError:
        pass


_COMMIT_LINE_RE = re.compile(r"^\[[^\]\n]+ [0-9a-f]{7,40}\] ", re.M)


def is_git_commit(command, stdout):
    """True when the command ran `git commit` and it evidently succeeded (PostToolUse = non-error exit).
    The exit status vouches only for a final `git commit` with nothing after it; otherwise (`; echo`,
    `| tail`, `|| true` can mask a failed commit) git's '[branch sha]' summary must be in stdout."""
    segs = _segments(command)
    for idx, (words, piped) in enumerate(segs):
        words = _strip_wrappers(words)
        if piped or not words or os.path.basename(words[0]) != "git":
            continue
        k = 1
        while k < len(words) and words[k].startswith("-"):
            k += 2 if words[k] in ("-C", "-c") else 1
        if k < len(words) and words[k] == "commit" and "--dry-run" not in words:
            last = idx == len(segs) - 1
            return last or bool(isinstance(stdout, str) and _COMMIT_LINE_RE.search(stdout))
    return False


def _render(res, log):
    """Model-facing stdout: squeezed text plus the footer."""
    footer = "[hetz squeeze %d→%d lines · raw: %s (grep it)%s]" % (
        res["lines_in"], res["lines_out"], log, " · quieter: " + res["tip"] if res["tip"] else "")
    return res["stdout"] + "\n" + footer if res["stdout"] else footer


def rewrite(command, resp, sid, tuid):
    """The updatedToolOutput object, or None to pass through. Writes the raw log and ledger row."""
    if resp.get("interrupted") or resp.get("isImage") or resp.get("backgroundTaskId") \
            or resp.get("structuredContent") or not sid or not tuid:
        return None
    stdout, stderr = resp.get("stdout"), resp.get("stderr", "")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return None
    persisted = resp.get("persistedOutputPath")
    full = _read_text(persisted, FULL_READ_MAX) if persisted else stdout
    if full is None:
        return None
    # What the model would read without us. Over the cap it gets only a preview, so an inlined result
    # must be no bigger than that preview; a tighter stage 2 gets a second chance before passing through.
    seen = (min(len(stdout), PREVIEW_CHARS) if persisted else len(stdout)) + len(stderr)
    res = squeeze(command, full, stderr)
    if res is None:
        return None
    logs_root = os.path.join(hetzlib.data_dir(), "logs")
    log = os.path.join(logs_root, sid, tuid + ".log")
    new_out = _render(res, log)
    if persisted and len(new_out) + len(res["stderr"]) > seen:
        res = squeeze(command, full, stderr, tight=True)
        if res is None:
            return None
        new_out = _render(res, log)
        if len(new_out) + len(res["stderr"]) > seen:
            return None
    fresh = not os.path.isdir(os.path.join(logs_root, sid))
    hetzlib.data_dir("logs", sid)
    if not _write_raw(log, command, full, stderr):
        return None
    if fresh:
        _prune_logs(logs_root)
    out = dict(resp)
    out["stdout"], out["stderr"] = new_out, res["stderr"]
    if persisted:
        out.pop("persistedOutputPath", None)
        out.pop("persistedOutputSize", None)
    _ledger(sid, command, seen, len(new_out) + len(res["stderr"]))
    return out


def main():
    data = hetzlib.read_input()
    if data.get("tool_name") != "Bash" or data.get("hook_event_name", "PostToolUse") != "PostToolUse":
        return
    resp, tin = data.get("tool_response"), data.get("tool_input")
    command = tin.get("command") if isinstance(tin, dict) else None
    if not isinstance(resp, dict) or not isinstance(command, str):
        return
    sid = _safe(data.get("session_id"))
    if sid and not resp.get("interrupted") and not hetzlib.env_off("HETZ_GUARD") \
            and is_git_commit(command, resp.get("stdout")):
        try:
            flag = os.path.join(hetzlib.data_dir("state"), sid + ".commit")
            with open(flag, "a"):
                pass
            os.utime(flag, None)
        except OSError:
            pass
    if hetzlib.env_off("HETZ_SQUEEZE"):
        return
    out = rewrite(command, resp, sid, _safe(data.get("tool_use_id")))
    if out is not None:
        hetzlib.emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": out}})


if __name__ == "__main__":
    hetzlib.run(main)
