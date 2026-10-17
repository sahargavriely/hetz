# Shared helpers, sourced by tasks/*/setup.sh and tasks/*/check.sh. POSIX sh; no network.

# fixture_dir [DIR]: create (or mktemp) an empty fixture dir, cd into it (current shell), set DIR.
fixture_dir() {
  if [ -n "${1:-}" ]; then
    mkdir -p "$1" && cd "$1" || exit 1
  else
    cd "$(mktemp -d "${TMPDIR:-/tmp}/hetz-task.XXXXXX")" || exit 1
  fi
  [ -z "$(ls -A)" ] || { echo "fixture dir not empty: $(pwd)" >&2; exit 1; }
  DIR=$(pwd)
}

# fixture_commit [MSG]: git-init cwd and commit everything with a pinned identity and date,
# isolated from the user's git config (no signing, no hooks).
fixture_commit() {
  (
    export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
    export GIT_AUTHOR_NAME=bench GIT_AUTHOR_EMAIL=bench@example.invalid
    export GIT_COMMITTER_NAME=bench GIT_COMMITTER_EMAIL=bench@example.invalid
    export GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z
    git init -q -b main . && git add -A && git commit -q -m "${1:-fixture}"
  )
}

# unchanged PATH...: exit 0 when PATHs match the fixture's first commit (tracked and untracked).
unchanged() {
  root=$(git rev-list --max-parents=0 HEAD 2>/dev/null | tail -1) || return 1
  git diff --quiet "$root" -- "$@" || return 1
  [ -z "$(git ls-files --others --exclude-standard -- "$@")" ]
}

# abspath PATH: PATH made absolute against the current dir (empty stays empty).
abspath() {
  case "$1" in
    /* | '') printf '%s' "$1" ;;
    *) printf '%s/%s' "$(pwd)" "$1" ;;
  esac
}

# fail MSG: print a check failure and exit 1.
fail() {
  printf 'FAIL: %s\n' "$*"
  exit 1
}
