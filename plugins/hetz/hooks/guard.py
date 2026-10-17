"""hetz session-guard: SessionStart, UserPromptSubmit, Stop and PreModelSwitch in one script.

Dispatches on hook_event_name. Stdout is a single JSON object or nothing: plain stdout on
SessionStart/UserPromptSubmit would enter the model's context. systemMessage reaches the user only.
  SessionStart (all sources)  quiet-tool exports appended to $CLAUDE_ENV_FILE (Bash only); prune old logs/state
  SessionStart startup|clear  one-time additionalContext pointer to a fresh, untracked .claude/handoff.md
  SessionStart resume|fork    cold-cache warning with the re-cache cost
  Stop / UserPromptSubmit     context-size (100k/150k) and commit-boundary nudges, max 3 per session; Stop
                              (end of turn) is first, UserPromptSubmit catches what it missed (no Stop wired,
                              interrupted turn). Stop only ever returns systemMessage: anything else continues the turn.
  PreModelSwitch              re-cache cost of the switch (never blocks or asks)
Switches: HETZ_GUARD=0 silences the warnings and nudges; HETZ_QUIET_ENV=off|aggressive.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hetzlib  # noqa: E402

QUIET_ENV = (
    # No FORCE_COLOR=0: rich treats any FORCE_COLOR value as a terminal and animates progress bars into the output.
    ("NO_COLOR", "1"), ("PY_COLORS", "0"),
    ("CARGO_TERM_COLOR", "never"), ("CARGO_TERM_PROGRESS_WHEN", "never"),
    ("PAGER", "cat"), ("GIT_PAGER", "cat"),
    ("NPM_CONFIG_FUND", "false"), ("NPM_CONFIG_PROGRESS", "false"), ("NPM_CONFIG_UPDATE_NOTIFIER", "false"),
    ("PIP_DISABLE_PIP_VERSION_CHECK", "1"), ("PIP_PROGRESS_BAR", "off"), ("UV_NO_PROGRESS", "1"),
    ("HOMEBREW_NO_ENV_HINTS", "1"), ("DOCKER_CLI_HINTS", "false"), ("TF_IN_AUTOMATION", "1"),
    ("NEXT_TELEMETRY_DISABLED", "1"), ("DOTNET_NOLOGO", "1"),
)
AGGRESSIVE_ENV = (("CI", "1"), ("PYTEST_ADDOPTS", "-q -rfE"), ("NPM_CONFIG_AUDIT", "false"))

HANDOFF_REL = os.path.join(".claude", "handoff.md")
HANDOFF_TTL = 12 * 3600
PRUNE_AGE = 7 * 86400
SIZE_THRESHOLDS = (100_000, 150_000)
MAX_MESSAGES = 3
COLD_RESUME_MIN = 60_000
COMMIT_MIN = 40_000
SWITCH_MIN = 30_000
SWITCH_DEDUPE_S = 5
AUTOMATED_PROMPTS = ("loop_wakeup", "schedule_wakeup", "poll_event", "system")


# ---------- small helpers ----------

def guard_off():
    return hetzlib.env_off("HETZ_GUARD")


def num(value):
    """int for real numbers (bool excluded), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def pos_float(value):
    """float for positive real numbers (bool excluded), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def ktok(n):
    return f"{round(n / 1000)}k" if n >= 1000 else str(int(n))


def usd(x):
    return f"${x:.2f}" if x >= 0.01 else f"${x:.3f}"


def usd_range(lo, hi):
    if lo is None:
        return ""
    if hi is None or abs(hi - lo) < 0.005:
        return f"~{usd(lo)}"
    return f"~{usd(lo)}–{usd(hi).lstrip('$')}"


def session_ok(sid):
    return isinstance(sid, str) and sid and "/" not in sid and "\\" not in sid and not sid.startswith(".")


def state_dir():
    return hetzlib.data_dir("state")


def load_state(sid):
    if not session_ok(sid):
        return None
    st = hetzlib.load_json(os.path.join(state_dir(), sid + ".json"), {})
    return st if isinstance(st, dict) else {}


def save_state(sid, st):
    if session_ok(sid):
        hetzlib.save_json(os.path.join(state_dir(), sid + ".json"), st)


def ctx_of(usage):
    try:
        return hetzlib.context_tokens(usage)
    except Exception:
        return 0


def tail_state(transcript_path, tail_bytes=262144):
    """(usage, ctx) the next request re-sends, from the transcript tail, like Claude Code's own meter.

    usage: last real main-thread assistant usage (+ "model"); synthetic/zero-usage records are skipped.
    A compact_boundary met first means no reply since the compaction: (None, its postTokens or 0).
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return None, 0
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return None, 0
    for line in reversed(lines):
        if '"compact_boundary"' not in line and '"usage"' not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict) or rec.get("isSidechain") is True:
            continue
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            meta = rec.get("compactMetadata")
            post = num(meta.get("postTokens")) if isinstance(meta, dict) else None
            return None, max(post or 0, 0)
        msg = rec.get("message")
        if (not isinstance(msg, dict) or msg.get("role") != "assistant" or not isinstance(msg.get("usage"), dict)
                or msg.get("model") == "<synthetic>"):
            continue
        usage = dict(msg["usage"])
        usage["model"] = msg.get("model")
        ctx = ctx_of(usage)
        if ctx > 0:
            return usage, ctx
    return None, 0


def first_ctx(transcript_path, head_bytes=131072):
    """Context size of the session's first real request (fresh-session baseline), or 0."""
    try:
        with open(transcript_path, "rb") as f:
            lines = f.read(head_bytes).decode("utf-8", "replace").splitlines()
    except Exception:
        return 0
    for line in lines:
        if '"usage"' not in line:
            continue
        try:
            msg = json.loads(line).get("message")
        except Exception:
            continue
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("model") != "<synthetic>":
            ctx = ctx_of(msg.get("usage") if isinstance(msg.get("usage"), dict) else None)
            if ctx:
                return ctx
    return 0


def usage_ttl(usage):
    """'1h' / '5m' from the last request's cache-write breakdown, else None."""
    cc = (usage or {}).get("cache_creation")
    if not isinstance(cc, dict):
        return None
    if (num(cc.get("ephemeral_1h_input_tokens")) or 0) > 0:
        return "1h"
    if (num(cc.get("ephemeral_5m_input_tokens")) or 0) > 0:
        return "5m"
    return None


# ---------- prices ----------

def norm_model(model):
    if not isinstance(model, str):
        return ""
    m = re.sub(r"\[[^\]]*\]\s*$", "", model.strip().lower())
    i = m.find("claude-")
    return m[i:] if i >= 0 else m


def price_for(model, ctx=0):
    """prices.json row for a model id (longest matching key prefix), or None."""
    m = norm_model(model)
    if not m:
        return None
    prices = hetzlib.load_prices()
    table = prices.get("models") if isinstance(prices, dict) else None
    if not isinstance(table, dict):
        return None
    keys = [k for k in table if isinstance(k, str) and m.startswith(k)]
    if not keys:
        return None
    best = max(keys, key=len)
    if ctx > 100_000:
        over = (prices.get("_over_100k") or {}).get(best)
        if isinstance(over, dict):
            return over
    row = table.get(best)
    return row if isinstance(row, dict) else None


def model_label(model):
    m = re.search(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?!\d)", norm_model(model))
    if not m:
        return norm_model(model) or "the new model"
    return m.group(1).title() + " " + m.group(2) + ("." + m.group(3) if m.group(3) else "")


def write_cost(ctx, row, ttl):
    """(lo, hi) USD to re-cache ctx tokens; a range when the cache TTL is unknown."""
    try:
        w5 = ctx * float(row["cache_write_5m"]) / 1e6
        w1 = ctx * float(row["cache_write_1h"]) / 1e6
    except Exception:
        return None, None
    if ttl == "5m":
        return w5, w5
    if ttl == "1h":
        return w1, w1
    return w5, w1


# ---------- SessionStart ----------

def quiet_env():
    """Append `export K=V` for unset quiet-tool vars to $CLAUDE_ENV_FILE (sourced before each Bash call)."""
    mode = os.environ.get("HETZ_QUIET_ENV", "").strip().lower()
    path = os.environ.get("CLAUDE_ENV_FILE")
    if hetzlib.env_off("HETZ_QUIET_ENV") or not path:
        return
    pairs = QUIET_ENV + (AGGRESSIVE_ENV if mode == "aggressive" else ())
    try:
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    except OSError:
        existing = ""
    have = set(existing.splitlines())
    lines = [f"export {k}={shlex.quote(v)}" for k, v in pairs if k not in os.environ]
    lines = [ln for ln in lines if ln not in have]
    if not lines:
        return
    prefix = "\n" if existing and not existing.endswith("\n") else ""
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + "\n".join(lines) + "\n")


def prune(now):
    """Delete hetz log/state files older than 7 days; runs at most once a day."""
    sdir = state_dir()
    stamp = os.path.join(sdir, "prune.stamp")
    try:
        if now - os.path.getmtime(stamp) < 86400:
            return
    except OSError:
        pass
    try:
        with open(stamp, "w"):
            pass
    except OSError:
        return
    cutoff = now - PRUNE_AGE
    for root in (sdir, hetzlib.data_dir("logs")):
        for dirpath, _dirs, files in os.walk(root, topdown=False):
            for name in files:
                p = os.path.join(dirpath, name)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                except OSError:
                    pass
            if dirpath != root:
                try:
                    os.rmdir(dirpath)  # only succeeds when empty
                except OSError:
                    pass


def session_cwd(inp):
    cwd = inp.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def clip(text, limit=100):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().replace('"', "'")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def handoff_goal(text):
    lines = [ln.strip() for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        m = re.match(r"^[#*>\-\s]*goal\b[\s*:\-–—]*(.*)$", ln, re.I)
        if m:
            goal = m.group(1).strip(" *#")
            if not goal:
                goal = next((x for x in lines[i + 1:] if x.strip("#*- ")), "")
            return clip(goal.strip("#*- "))
    first = next((x for x in lines if x.strip("#*- ")), "")
    return clip(first.strip("#*- "))


def age_text(seconds):
    minutes = max(1, int(seconds // 60))
    return f"{minutes} min ago" if minutes < 90 else f"{round(minutes / 60)} h ago"


def git_tracked(cwd, rel):
    """True when rel is in cwd's git index; False when untracked, outside a repo or git is unusable."""
    try:
        p = subprocess.run(["git", "-C", cwd, "ls-files", "--error-unmatch", "--", rel],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=2,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
        return p.returncode == 0
    except Exception:
        return False


def handoff_pickup(inp, now):
    """additionalContext pointing at a fresh, not-yet-offered, untracked .claude/handoff.md, else None.

    A git-tracked note is a team note (a checkout/pull makes its mtime fresh), not this user's last session.
    """
    cwd = session_cwd(inp)
    path = os.path.join(cwd, HANDOFF_REL)
    try:
        st = os.stat(path)
        if not os.path.isfile(path):
            return None
        age = now - st.st_mtime
        if age > HANDOFF_TTL or age < -300:
            return None
        with open(path, "rb") as f:
            data = f.read(65536)
    except OSError:
        return None
    if not data.strip():
        return None
    digest = hashlib.sha256(data).hexdigest()[:16]
    consumed_path = os.path.join(state_dir(), "consumed.json")
    consumed = hetzlib.load_json(consumed_path, {})
    if not isinstance(consumed, dict):
        consumed = {}
    if digest in consumed or git_tracked(cwd, ".claude/handoff.md"):
        return None
    consumed = {k: v for k, v in consumed.items() if isinstance(v, (int, float)) and now - v < PRUNE_AGE}
    consumed[digest] = now
    hetzlib.save_json(consumed_path, consumed)
    goal = handoff_goal(data.decode("utf-8", "replace"))
    detail = f"written {age_text(age)}" + (f'; goal: "{goal}"' if goal else "")
    return (f"A handoff note from an earlier session in this repo is at .claude/handoff.md ({detail}). "
            "It applies only if the user continues that work.")


def cold_resume(inp, now):
    """systemMessage text when resuming a big transcript whose prompt cache has expired, else None."""
    expired = inp.get("prompt_cache_likely_expired")
    ctx = num(inp.get("context_tokens"))
    usage, tail_ctx = tail_state(inp.get("transcript_path"))
    if ctx is None:
        ctx = tail_ctx
    if not isinstance(expired, bool):  # pre-2.1.27x input: compare transcript age with the cache TTL
        ttl = usage_ttl(usage) or ("5m" if os.environ.get("ANTHROPIC_API_KEY") else "1h")
        try:
            idle = now - os.path.getmtime(inp.get("transcript_path"))
        except (OSError, TypeError):
            return None
        expired = idle >= (3600 if ttl == "1h" else 300)
    if not expired or not ctx or ctx < COLD_RESUME_MIN:
        return None
    model = inp.get("model") if price_for(inp.get("model")) else (usage or {}).get("model")
    row = price_for(model, ctx)
    if row:
        lo, hi = write_cost(ctx, row, usage_ttl(usage))
    else:
        lo = hi = pos_float(inp.get("estimated_cache_write_usd"))
    cost = usd_range(lo, hi)
    cost = f" ({cost})" if cost else ""
    if os.path.isfile(os.path.join(session_cwd(inp), HANDOFF_REL)):
        tail = "If you only need conclusions: /clear and continue from .claude/handoff.md."
    else:
        tail = "If you only need conclusions: /clear (free) and restate them."
    return f"hetz: resuming ~{ktok(ctx)} tokens with a cold cache; the first request re-writes it{cost}. {tail}"


def on_session_start(inp):
    now = time.time()
    source = inp.get("source")
    out = {}
    for step in (quiet_env, lambda: prune(now)):
        try:
            step()
        except Exception:
            pass
    if source in ("startup", "clear"):
        ctx_text = handoff_pickup(inp, now)
        if ctx_text:
            out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext": ctx_text}
    elif source in ("resume", "fork") and not guard_off():
        msg = cold_resume(inp, now)
        if msg:
            out["systemMessage"] = msg
    elif source == "compact":  # context shrank: let the size nudges fire again (still capped per session)
        st = load_state(inp.get("session_id"))
        if st and st.get("crossed"):
            st["crossed"] = []
            save_state(inp.get("session_id"), st)
    return out


# ---------- Stop / UserPromptSubmit ----------

def size_message(ctx, model):
    row = price_for(model, ctx)
    per_req = ""
    if row:
        try:
            per_req = f" (~{usd(ctx * float(row['cache_read']) / 1e6)}/req on {model_label(model)})"
        except Exception:
            per_req = ""
    return (f"hetz: context {ktok(ctx)}, re-sent on every request{per_req}. "
            "New task: /hetz:handoff, then /clear (free). Same task: /compact <focus> now, while the cache is warm.")


def boundary_message(ctx, transcript_path, late):
    base = first_ctx(transcript_path)
    drop = (f"drops ~{ktok(ctx)} → ~{ktok(base)} per request" if 0 < base < ctx * 0.8
            else f"stops re-sending ~{ktok(ctx)} per request")
    if late:  # the prompt already went out with the full context
        return f"hetz: task boundary (committed). If this prompt starts a new task: Esc, /clear, re-send; it {drop}."
    return f"hetz: task boundary (committed). Next task: /clear first; it {drop}."


def nudge(inp, late):
    """Size and commit-boundary nudges as {"systemMessage": ...} or {}; late = the prompt is already sent."""
    sid = inp.get("session_id")
    st = load_state(sid)
    if st is None:
        return {}
    flag = os.path.join(state_dir(), sid + ".commit")
    committed = os.path.exists(flag)
    if committed:
        try:
            os.remove(flag)
        except OSError:
            pass
    usage, ctx = tail_state(inp.get("transcript_path"))
    crossed = [t for t in st.get("crossed", []) if isinstance(t, int)]
    new = [t for t in SIZE_THRESHOLDS if ctx >= t and t not in crossed]
    msg = None
    if committed and ctx > COMMIT_MIN:
        msg = boundary_message(ctx, inp.get("transcript_path"), late)
    elif new:
        msg = size_message(ctx, (usage or {}).get("model"))
    if not new and msg is None:
        return {}
    st["crossed"] = sorted(set(crossed + new))
    out = {}
    if msg and int(st.get("messages") or 0) < MAX_MESSAGES:
        st["messages"] = int(st.get("messages") or 0) + 1
        out["systemMessage"] = msg
    save_state(sid, st)
    return out


def on_stop(inp):
    if guard_off():
        return {}
    out = nudge(inp, late=False)
    return {"systemMessage": out["systemMessage"]} if out.get("systemMessage") else {}


def on_prompt(inp):
    if guard_off():
        return {}
    prompt = inp.get("prompt")
    if inp.get("source") in AUTOMATED_PROMPTS or (isinstance(prompt, str) and prompt.lstrip().startswith("/")):
        return {}
    return nudge(inp, late=True)


# ---------- PreModelSwitch ----------

def on_model_switch(inp):
    if guard_off() or inp.get("prompt_cache_warm") is False:
        return {}
    ctx = num(inp.get("context_tokens"))
    if ctx is None:
        ctx = tail_state(inp.get("transcript_path"))[1]
    if not ctx or ctx < SWITCH_MIN:
        return {}
    to_model = inp.get("to_model") or inp.get("requested_model") or ""
    sid = inp.get("session_id")
    st = load_state(sid)
    now = time.time()
    if st is not None:
        last = st.get("switch_ts")
        if isinstance(last, (int, float)) and 0 <= now - last < SWITCH_DEDUPE_S:
            return {}  # one /model can fire the hook twice (plan-mode model pairs)
        st["switch_ts"] = now
        save_state(sid, st)
    ttl = inp.get("cache_ttl") if inp.get("cache_ttl") in ("5m", "1h") else None
    est = pos_float(inp.get("estimated_cache_write_usd"))
    row = price_for(to_model, ctx)
    if est is not None and (inp.get("pricing") == "configured" or not row):
        lo = hi = est
    elif row:
        lo, hi = write_cost(ctx, row, ttl)
    else:
        lo = hi = None
    cost = usd_range(lo, hi)
    cost = f" ({cost})" if cost else ""
    # Shown with the switch result (after Claude Code's own "Switch model?" confirm), before the next request.
    return {"systemMessage": (
        f"hetz: switching to {model_label(to_model)} re-sends ~{ktok(ctx)} tokens uncached{cost}; "
        "the warm cache is wasted. /model back is free; cheaper switch: /hetz:handoff → /clear → new model.")}


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_prompt,
    "Stop": on_stop,
    "PreModelSwitch": on_model_switch,
}


def main():
    inp = hetzlib.read_input()
    handler = HANDLERS.get(inp.get("hook_event_name"))
    if handler is None:
        return
    out = handler(inp)
    if out:
        hetzlib.emit(out)


if __name__ == "__main__":
    hetzlib.run(main)
