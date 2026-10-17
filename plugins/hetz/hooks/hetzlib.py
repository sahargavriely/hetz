"""Shared helpers for hetz hooks. Python 3 stdlib only; every helper is total (never raises).

Hooks run as `python3 -I <hook>.py`, so the hooks dir is not on sys.path; import with:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import hetzlib
"""
import json
import os
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
OFF_VALUES = ("0", "off", "false", "no")


def env_off(name):
    """True when a HETZ_* switch is explicitly disabled (HETZ_SQUEEZE=0 etc.)."""
    return os.environ.get(name, "").strip().lower() in OFF_VALUES


def data_dir(*parts):
    """Persistent plugin data dir (${CLAUDE_PLUGIN_DATA}, else ~/.claude/hetz/data), created on demand."""
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(os.path.expanduser("~"), ".claude", "hetz", "data")
    path = os.path.join(base, *parts)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def read_input():
    """Parse the hook's JSON stdin; {} on anything unexpected."""
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def emit(obj):
    """Print one JSON object to stdout (the only stdout a hook should produce)."""
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:
        pass


def load_prices():
    """hooks/prices.json: USD per million tokens by model id; see that file for the schema."""
    return load_json(os.path.join(HOOKS_DIR, "prices.json"), {}) or {}


def last_usage(transcript_path, tail_bytes=262144):
    """Usage dict of the last assistant message in a transcript JSONL (reads only the tail), or None."""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        if isinstance(msg, dict) and msg.get("role") == "assistant" and isinstance(msg.get("usage"), dict):
            usage = dict(msg["usage"])
            usage["model"] = msg.get("model")
            return usage
    return None


def context_tokens(usage):
    """Tokens re-sent on the next request: input + cache read + cache creation."""
    if not usage:
        return 0
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def run(main):
    """Fail-open entry point: any exception becomes a silent exit 0."""
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
