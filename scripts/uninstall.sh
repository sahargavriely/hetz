#!/usr/bin/env bash
# hetz uninstaller (works without Claude): undoes every settings/file change recorded in
# ~/.claude/hetz/manifest.json, uninstalls hetz-crew and hetz, removes the hetz marketplace.
# Usage: scripts/uninstall.sh [--dry-run] [--keep-settings] [--purge]
#   --dry-run        print what would happen (the undo preview is read-only)
#   --keep-settings  leave hetz settings in place
#   --purge          also delete ~/.claude/hetz (hook logs, ledger, manifest backups)
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${HETZ_HOME:-$HOME}"
HETZ_DIR="$HOME_DIR/.claude/hetz"
SETTINGS_PY="$REPO_DIR/plugins/hetz/scripts/hetz_settings.py"
DRY=0 KEEP=0 PURGE=0
for arg in "$@"; do
  case "$arg" in
    -n|--dry-run) DRY=1 ;;
    --keep-settings) KEEP=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "uninstall.sh: unknown option $arg" >&2; exit 2 ;;
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

if [ "$KEEP" = 0 ]; then
  if [ ! -f "$HETZ_DIR/manifest.json" ]; then
    say "= no hetz manifest: no settings to undo"
  elif ! command -v python3 >/dev/null 2>&1; then
    say "python3 not found: cannot undo settings; restore from ~/.claude/settings.json.hetz-*.bak (repo files: ~/.claude/hetz/backups/) by hand" >&2
  elif [ "$DRY" = 1 ]; then
    HETZ_HOME="$HOME_DIR" python3 -I "$SETTINGS_PY" undo --scope all --dry-run
  else
    HETZ_HOME="$HOME_DIR" python3 -I "$SETTINGS_PY" undo --scope all || { say "undo failed; stopping before uninstall" >&2; exit 1; }
  fi
fi

if command -v claude >/dev/null 2>&1; then
  installed=$(claude plugin list --json 2>/dev/null || true)
  for plugin in hetz-crew hetz; do
    if has_plugin "$plugin@hetz" "$installed"; then
      run claude plugin uninstall "$plugin@hetz"
    else
      say "= $plugin@hetz not installed"
    fi
  done
  markets=$(claude plugin marketplace list --json 2>/dev/null || true)
  if printf '%s' "$markets" | grep -Eq '"name": *"hetz"'; then
    run claude plugin marketplace remove hetz
  else
    say "= marketplace hetz not present"
  fi
else
  say "claude not on PATH: skipping plugin uninstall"
fi

if [ "$PURGE" = 1 ]; then
  [ -d "$HETZ_DIR" ] && run rm -rf "$HETZ_DIR"
elif [ -f "$HETZ_DIR/py" ]; then
  run rm -f "$HETZ_DIR/py"
fi
say "hetz removed. Restart open Claude Code sessions."
