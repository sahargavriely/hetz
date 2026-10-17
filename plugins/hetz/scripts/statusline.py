#!/usr/bin/env python3
"""hetz statusline: `sonnet·med │ ctx 142k/200k │ cache 96% warm │ $3.12` (stdlib; never raises; one line).

Installed by /hetz:setup as ~/.claude/hetz/statusline.py (the plugin root moves on updates).
Reads Claude Code's statusLine stdin JSON (2.1.272): model.id, effort.level, context_window.{current_usage,
context_window_size, total_input_tokens}, prompt_cache.{warm, hit_ratio, requests}, cost.total_cost_usd.
--wrap: run the previous statusLine command saved in statusline-wrap.json beside this file, then append ours.
--no-color or NO_COLOR: plain text.
"""
import json
import os
import sys

SEP = " │ "
WARN_TOKENS = 80000
ALERT_TOKENS = 150000
MIN_WINDOW, MAX_WINDOW = 100000, 1000000  # autoCompactWindow bounds in 2.1.272
FAMILIES = ("opus", "sonnet", "haiku", "fable")
EFFORT_SHORT = {"medium": "med", "xhigh": "xhi"}
WRAP_FILE = "statusline-wrap.json"


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _dict(value):
    return value if isinstance(value, dict) else {}


def paint(text, code, on):
    return "\033[%sm%s\033[0m" % (code, text) if on else text


def fmt_tokens(n):
    if n >= 1000000:
        s = "%.1f" % (n / 1000000.0)
        return (s[:-2] if s.endswith(".0") else s) + "M"
    if n >= 1000:
        return "%dk" % int(round(n / 1000.0))
    return str(n)


def model_label(data):
    model = data.get("model")
    mid, display = "", ""
    if isinstance(model, dict):
        mid = str(model.get("id") or "")
        display = str(model.get("display_name") or "")
    elif isinstance(model, str):
        mid = model
    low = (mid + " " + display).lower()
    name = next((f for f in FAMILIES if f in low), "")
    if not name:
        name = (display.split() or [mid])[0].lower()
    if not name:
        return ""
    effort = data.get("effort")
    level = effort.get("level") if isinstance(effort, dict) else None
    if level is not None and str(level).strip():
        level = str(level).lower()
        name += "·" + EFFORT_SHORT.get(level, level)
    return name


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def compact_window(data, home):
    """autoCompactWindow as 2.1.272 resolves it: env > local > project > user settings (invalid values ignored)."""
    env = _int(os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"))
    if env is not None and env > 0:
        return max(MIN_WINDOW, min(env, MAX_WINDOW))
    ws = _dict(data.get("workspace"))
    project = ws.get("project_dir") or ws.get("current_dir") or data.get("cwd")
    files = [os.path.join(home, ".claude", "settings.json")]
    if isinstance(project, str) and project:
        files += [os.path.join(project, ".claude", "settings.json"), os.path.join(project, ".claude", "settings.local.json")]
    value = None
    for path in files:
        v = _load(path).get("autoCompactWindow")
        if isinstance(v, int) and not isinstance(v, bool) and MIN_WINDOW <= v <= MAX_WINDOW:
            value = v
    return value


def context_tokens(data):
    cw = _dict(data.get("context_window"))
    usage = cw.get("current_usage")
    if isinstance(usage, dict):
        return sum(_int(usage.get(k)) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return _int(cw.get("total_input_tokens"))


def ctx_segment(data, home, color):
    tokens = context_tokens(data)
    size = _int(_dict(data.get("context_window")).get("context_window_size"))
    window = compact_window(data, home)
    if window and size:
        window = min(window, size)
    window = window or size
    if tokens is None:
        return "ctx ?" + ("/" + fmt_tokens(window) if window else ""), 0
    text = "ctx " + fmt_tokens(tokens) + ("/" + fmt_tokens(window) if window else "")
    if tokens > ALERT_TOKENS:
        return paint(text + " → /hetz:handoff + /clear", "31", color), tokens
    if tokens >= WARN_TOKENS:
        return paint(text, "33", color), tokens
    return paint(text, "2", color), tokens


def cache_segment(data, tokens, color):
    pc = data.get("prompt_cache")
    if isinstance(pc, dict) and pc:
        if pc.get("warm") is False:
            return paint("cache COLD", "31" if tokens >= WARN_TOKENS else "33", color)
        ratio = pc.get("hit_ratio")
        pct = None
        if isinstance(ratio, (int, float)) and not isinstance(ratio, bool):
            pct = int(round(ratio * 100 if ratio <= 1 else ratio))
        text = "cache %d%% warm" % pct if pct is not None else "cache warm"
        if pct is not None and pct < 80 and (_int(pc.get("requests")) or 0) >= 3:
            return paint(text, "31", color)
        return text
    usage = _dict(_dict(data.get("context_window")).get("current_usage"))
    read = _int(usage.get("cache_read_input_tokens")) or 0
    total = read + (_int(usage.get("cache_creation_input_tokens")) or 0) + (_int(usage.get("input_tokens")) or 0)
    if total > 0:
        return "cache %d%%" % int(round(100.0 * read / total))
    return None


def cost_segment(data):
    cost = _dict(data.get("cost")).get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        return "$%.2f" % cost
    return None


def render(data, home, color):
    """Model first; ctx/cache/cost only when Claude Code sent those objects (bad input -> model name only)."""
    parts = [model_label(data)]
    if isinstance(data.get("context_window"), dict):
        ctx, tokens = ctx_segment(data, home, color)
        parts += [ctx, cache_segment(data, tokens, color)]
    parts.append(cost_segment(data))
    return SEP.join(p for p in parts if p)


def wrapped_output(raw):
    """stdout of the user's previous statusLine command (saved by /hetz:setup), or None."""
    if os.environ.get("HETZ_STATUSLINE_WRAPPED"):  # the wrapped command is hetz itself: never recurse
        return None
    cfg = _load(os.path.join(os.path.dirname(os.path.abspath(__file__)), WRAP_FILE))
    command = cfg.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        import subprocess

        env = dict(os.environ, HETZ_STATUSLINE_WRAPPED="1")
        proc = subprocess.run(command, shell=True, input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5, env=env)
        out = proc.stdout.decode("utf-8", "replace").rstrip("\n")
        return out or None
    except Exception:
        return None


def main(argv):
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        raw = b""
    try:
        data = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    color = "--no-color" not in argv and not os.environ.get("NO_COLOR")
    home = os.environ.get("HETZ_HOME") or os.path.expanduser("~")
    try:
        line = render(data, home, color)
    except Exception:
        try:
            line = model_label(data)
        except Exception:
            line = ""
    if "--wrap" in argv:
        prev = wrapped_output(raw)
        if prev:
            lines = prev.split("\n")
            lines[0] = lines[0] + SEP + line if line else lines[0]
            line = "\n".join(lines)
    out = sys.stdout.buffer  # UTF-8 regardless of locale: a non-UTF-8 locale must not blank the bar
    out.write((line + "\n").encode("utf-8", "replace"))
    out.flush()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        pass
    sys.exit(0)
