#!/usr/bin/env bash
# hetz gate: run before every commit (and in CI). Exits non-zero if any check fails.
#   scripts/check.sh            all checks
#   HETZ_NO_CLI=1 scripts/check.sh   skip checks that need the `claude` CLI (validate, details)
# Free: no model calls. bash 3.2 compatible.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PY=${PYTHON:-python3}
FAILED=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/hetz-check.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

step() { printf '\n== %s\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAILED=1; }
note() { printf '  note  %s\n' "$*"; }

have_cli() { [ -z "${HETZ_NO_CLI:-}" ] && command -v claude >/dev/null 2>&1; }

# ---------------------------------------------------------------- JSON + frontmatter
step "JSON files parse; plugin.json has no version; frontmatter"
if "$PY" -I - "$ROOT" <<'EOF'
import json, os, re, sys
root = sys.argv[1]
bad = []
for d, dirs, files in os.walk(root):
    dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "results", "__pycache__", ".pytest_cache")]
    for f in files:
        if f.endswith(".json"):
            p = os.path.join(d, f)
            try:
                json.load(open(p, encoding="utf-8"))
            except Exception as e:
                bad.append(f"{os.path.relpath(p, root)}: invalid JSON ({e})")
for plug in ("hetz", "hetz-crew"):
    m = json.load(open(os.path.join(root, "plugins", plug, ".claude-plugin", "plugin.json")))
    if m.get("name") != plug:
        bad.append(f"plugins/{plug}/.claude-plugin/plugin.json: name is {m.get('name')!r}")
    if "version" in m:
        bad.append(f"plugins/{plug}/.claude-plugin/plugin.json: has 'version' (directory marketplace is versioned by git sha)")

def front(p):
    t = open(p, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---\n", t, re.S)
    if not m:
        return None
    out = {}
    for ln in m.group(1).splitlines():
        k = re.match(r"([A-Za-z][\w-]*):\s*(.*)$", ln)
        if k:
            out[k.group(1)] = k.group(2).strip()
    return out

for plug in ("hetz", "hetz-crew"):
    sk = os.path.join(root, "plugins", plug, "skills")
    for name in sorted(os.listdir(sk)):
        p = os.path.join(sk, name, "SKILL.md")
        if not os.path.isfile(p):
            continue
        rel = os.path.relpath(p, root)
        fm = front(p)
        if fm is None or not fm.get("description"):
            bad.append(f"{rel}: missing frontmatter or description")
            continue
        if plug == "hetz" and fm.get("disable-model-invocation") != "true":
            bad.append(f"{rel}: needs 'disable-model-invocation: true' (hetz must add 0 listing tokens)")
    ag = os.path.join(root, "plugins", plug, "agents")
    for name in sorted(os.listdir(ag)) if os.path.isdir(ag) else []:
        p = os.path.join(ag, name)
        fm = front(p)
        if not fm or not fm.get("name") or not fm.get("description") or not fm.get("model"):
            bad.append(f"{os.path.relpath(p, root)}: agent needs name, description and model")
for b in bad:
    print("  FAIL  " + b)
sys.exit(1 if bad else 0)
EOF
then ok "manifests, JSON, SKILL.md and agent frontmatter"; else FAILED=1; fi

# ---------------------------------------------------------------- claude plugin validate
step "claude plugin validate (marketplace + each plugin)"
if have_cli; then
  for target in . plugins/hetz plugins/hetz-crew; do
    if claude plugin validate --json "$target" >"$TMP/validate.json" 2>"$TMP/validate.err" &&
       "$PY" -I - "$TMP/validate.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
m = d.get("manifest") or {}
issues = list(m.get("errors") or [])
# The only tolerated warning: no "version" (PACKAGING: a directory marketplace is versioned by git sha).
issues += [w for w in (m.get("warnings") or []) if not str(w.get("path", "")).endswith("version")]
for c in d.get("contents") or []:
    issues += list(c.get("errors") or []) + list(c.get("warnings") or [])
for i in issues:
    print("        " + json.dumps(i))
sys.exit(0 if d.get("success") and not issues else 1)
EOF
    then ok "validate $target"; else bad "validate $target"; cat "$TMP/validate.err" 2>/dev/null || true; fi
  done
else
  note "claude CLI unavailable or HETZ_NO_CLI set: skipped"
fi

# ---------------------------------------------------------------- listing budget
# `claude plugin details` counts every skill's name+description as always-on, but 2.1.272's model-facing
# listing drops disable-model-invocation skills. So: measure a copy of each plugin without those skills.
step "always-on listing budget (hetz = 0, hetz-crew <= 80)"
always_on() {  # $1 plugin dir, $2 plugin name -> prints the Always-on token count (or nothing)
  (cd "$TMP" && claude --plugin-dir "$1" plugin details "$2" 2>/dev/null) |
    sed -n 's/^ *Always-on: *[~<]* *\([0-9][0-9]*\) tok.*/\1/p' | head -1
}
model_facing_copy() {  # $1 plugin name -> path of a copy without disable-model-invocation skills
  local src="$ROOT/plugins/$1" dst="$TMP/mf/$1" s
  rm -rf "$dst"; mkdir -p "$dst"
  for s in "$src"/* "$src"/.claude-plugin; do
    case "$(basename "$s")" in tests|__pycache__) continue ;; esac
    cp -R "$s" "$dst/"
  done
  for s in "$dst"/skills/*/SKILL.md; do
    [ -f "$s" ] || continue
    if grep -q '^disable-model-invocation: *true' "$s"; then rm -rf "$(dirname "$s")"; fi
  done
  printf '%s' "$dst"
}
if have_cli; then
  for plug in hetz hetz-crew; do
    raw=$(always_on "$ROOT/plugins/$plug" "$plug")
    mf=$(always_on "$(model_facing_copy "$plug")" "$plug")
    if [ -z "$raw" ] || [ -z "$mf" ]; then bad "$plug: could not read 'claude plugin details' output"; continue; fi
    if [ "$plug" = hetz ]; then
      if [ "$mf" -eq 0 ]; then ok "hetz model-facing ~$mf tok (details total ~$raw incl. user-only skills)"
      else bad "hetz model-facing ~$mf tok, must be 0"; fi
    else
      if [ "$raw" -le 80 ]; then ok "hetz-crew details total ~$raw tok <= 80 (model-facing ~$mf)"
      else bad "hetz-crew details total ~$raw tok, must be <= 80"; fi
    fi
  done
else
  note "claude CLI unavailable or HETZ_NO_CLI set: skipped (SKILL.md flag check above still ran)"
fi

# ---------------------------------------------------------------- hooks fail open
step "hooks fail open via run.sh (garbage stdin -> exit 0, no stdout)"
for h in guard.py squeeze.py contract.py; do
  out=$(printf 'not json {' | CLAUDE_PLUGIN_DATA="$TMP/data" sh plugins/hetz/hooks/run.sh "$h") && rc=0 || rc=$?
  if [ "$rc" -eq 0 ] && [ -z "$out" ]; then ok "$h"; else bad "$h: rc=$rc stdout=${out:0:80}"; fi
done

# ---------------------------------------------------------------- pytest
step "pytest (hooks, scripts, skills, crew, ledger, bench)"
if "$PY" -m pytest --version >/dev/null 2>&1; then
  if "$PY" -m pytest -p no:cacheprovider -q plugins scripts/tests bench/tests >"$TMP/pytest.out" 2>&1; then
    ok "pytest: $(tail -1 "$TMP/pytest.out")"
  else
    tail -40 "$TMP/pytest.out"; bad "pytest"
  fi
else
  bad "pytest not installed ($PY -m pip install pytest)"
fi

# ---------------------------------------------------------------- shell scripts
step "shell scripts"
SH_FILES=$(find scripts plugins bench -name '*.sh' -not -path '*/node_modules/*' | sort)
if command -v shellcheck >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  if shellcheck -x -S warning $SH_FILES; then ok "shellcheck"; else bad "shellcheck"; fi
else
  note "shellcheck not installed: syntax check only"
  for f in $SH_FILES; do
    case "$(head -1 "$f")" in *bash*) chk=bash ;; *) chk=sh ;; esac
    if "$chk" -n "$f"; then :; else bad "$chk -n $f"; fi
  done
  ok "sh/bash -n on $(printf '%s\n' "$SH_FILES" | wc -l | tr -d ' ') scripts"
fi
L=plugins/hetz/templates/launchers
if bash -n "$L/hetz.bash"; then ok "bash -n hetz.bash"; else bad "bash -n hetz.bash"; fi
if command -v zsh >/dev/null 2>&1; then
  if zsh -n "$L/hetz.zsh"; then ok "zsh -n hetz.zsh"; else bad "zsh -n hetz.zsh"; fi
fi
if command -v fish >/dev/null 2>&1; then
  fok=1; for f in "$L"/fish/*.fish; do fish --no-execute "$f" || { bad "fish --no-execute $f"; fok=0; }; done
  [ "$fok" -eq 1 ] && ok "fish --no-execute launchers"
fi

# ---------------------------------------------------------------- evidence version
step "docs/EVIDENCE.md verified-against version"
ev=$(grep -o 'hetz:evidence-version [0-9.]*' docs/EVIDENCE.md | awk '{print $2}' | head -1 || true)
if have_cli; then
  cv=$(claude --version 2>/dev/null | awk '{print $1}')
  if [ "$ev" = "$cv" ]; then ok "EVIDENCE $ev = claude $cv"
  else note "EVIDENCE verified on ${ev:-?}, installed claude is ${cv:-?}: re-run the re-verify greps in docs/EVIDENCE.md"; fi
else
  note "EVIDENCE verified on ${ev:-?}"
fi

echo
if [ "$FAILED" -ne 0 ]; then echo "check.sh: FAILED"; exit 1; fi
echo "check.sh: all checks passed"
