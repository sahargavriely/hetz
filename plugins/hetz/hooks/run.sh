#!/bin/sh
# Fail-open hook launcher: a missing interpreter or a crashing hook must never block Claude.
P=$(cat "$HOME/.claude/hetz/py" 2>/dev/null)
[ -x "$P" ] || P=$(command -v python3) || exit 0
"$P" -I "$(dirname "$0")/$1" 2>/dev/null || exit 0
