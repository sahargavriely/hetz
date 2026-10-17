#!/usr/bin/env bash
# hetz installer: adds this repo as the "hetz" marketplace (directory source, loads in place) and installs
# hetz + hetz-crew at user scope, so they are active in every repo. Idempotent.
# Usage: scripts/install.sh [--dry-run]   (--dry-run prints the claude commands instead of running them)
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIN_VERSION="2.1.272"
HOME_DIR="${HETZ_HOME:-$HOME}"
DRY=0
for arg in "$@"; do
  case "$arg" in
    -n|--dry-run) DRY=1 ;;
    -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
# True when `claude plugin list --json` output ($2) lists plugin id $1 (shape-agnostic).
has_plugin() {
  printf '%s' "$2" | grep -q "\"$1\"" && return 0
  command -v python3 >/dev/null 2>&1 || return 1
  printf '%s' "$2" | python3 -I -c '
import json, sys
want = sys.argv[1]
name, market = want.split("@")
def walk(o):
    if isinstance(o, dict):
        if want in o or want in map(str, o.values()):
            return True
        if o.get("name") == name and market in (o.get("marketplace"), o.get("marketplaceName"), o.get("source")):
            return True
        return any(walk(v) for v in o.values())
    if isinstance(o, list):
        return any(walk(v) for v in o)
    return False
try:
    sys.exit(0 if walk(json.load(sys.stdin)) else 1)
except Exception:
    sys.exit(1)
' "$1"
}
run() {
  if [ "$DRY" = 1 ]; then printf '+ %s\n' "$*"; return 0; fi
  "$@"
}
version_lt() { # true when $1 < $2 (x.y.z)
  local IFS=.
  local -a a=($1) b=($2)
  local i x y
  for i in 0 1 2; do
    x=${a[i]:-0}; y=${b[i]:-0}
    if ((10#$x < 10#$y)); then return 0; fi
    if ((10#$x > 10#$y)); then return 1; fi
  done
  return 1
}

if ! command -v claude >/dev/null 2>&1; then
  say "claude is not on PATH: install Claude Code first (https://code.claude.com)." >&2
  [ "$DRY" = 1 ] || exit 1
else
  ver=$(claude --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1)
  if [ -z "$ver" ]; then
    say "warning: could not read claude --version; hetz targets $MIN_VERSION+."
  elif version_lt "$ver" "$MIN_VERSION"; then
    say "warning: claude $ver is older than $MIN_VERSION; autoCompactWindow and bashOutputMaxChars may be ignored. Run: claude update"
  fi
fi

markets=$(claude plugin marketplace list --json 2>/dev/null || true)
if printf '%s' "$markets" | grep -Eq '"name": *"hetz"'; then
  say "= marketplace hetz already added"
else
  run claude plugin marketplace add "$REPO_DIR" || { say "marketplace add failed" >&2; exit 1; }
fi

installed=$(claude plugin list --json 2>/dev/null || true)
for plugin in hetz hetz-crew; do
  if has_plugin "$plugin@hetz" "$installed"; then
    say "= $plugin@hetz already installed"
  else
    run claude plugin install "$plugin@hetz" || say "warning: claude plugin install $plugin@hetz failed (already installed?); check: claude plugin list"
  fi
done

# Real interpreter path (pyenv shims resolved) so hooks and the statusline skip shim latency.
PY_FILE="$HOME_DIR/.claude/hetz/py"
if py=$(command -v python3 2>/dev/null) && real=$("$py" -I -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null) && [ -n "$real" ]; then
  if [ "$(cat "$PY_FILE" 2>/dev/null)" = "$real" ]; then
    say "= $PY_FILE already points at $real"
  elif [ "$DRY" = 1 ]; then
    say "+ write $real to $PY_FILE"
  else
    mkdir -p "$(dirname "$PY_FILE")" && printf '%s\n' "$real" > "$PY_FILE"
    say "wrote $PY_FILE -> $real"
  fi
else
  say "warning: python3 not found; hetz hooks stay inactive (they fail open) and /hetz:setup needs python3."
fi

say "Next: open any Claude Code session and run /hetz:setup global"
