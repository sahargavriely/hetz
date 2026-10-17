# Quiet flags cheat sheet

Make commands print what Claude needs (result, failures, exit code) and nothing else. Every line a command prints is re-read on every later request in the session. Claude Code 2.1.272, 2026-10-17. Squeeze's footer tips come from `plugins/hetz/share/quiet-flags.tsv`, not from this page.

**Checked locally** (`--help`/`man`, 2026-10-17): git 2.47.0, pip 26.0.1, uv 0.12.5, pytest 8.4.1, GNU make 3.81, docker 29.7.2, kubectl, Homebrew 6.0.19, curl 8.7.1. The others come from official docs (links at the bottom), marked *docs*. Rows marked *docs, M* are not locally checked and are less certain.

## Rules that beat any flag

1. **Never cut output with a bare pipe.** In `cmd | tail -40` the exit code is tail's, so a failing build looks green. Use `set -o pipefail; cmd 2>&1 | tail -40`. Claude's Bash tool runs bash or zsh, so `pipefail` works there even if your login shell is fish.
2. **Log to a file, then look.** `cmd > "$TMPDIR/run.log" 2>&1; echo "exit=$?"; tail -40 "$TMPDIR/run.log"`. This keeps the exit code and the full log, and you can grep the log later.
3. **Don't silence stderr** (`2>/dev/null`) on build, test or install commands. The cause of a failure lives there.
4. **Quiet the passing run, not the failing one.** When something fails, rerun only the failing test or target verbosely.
5. **Summaries before content.** Use `git diff --stat` before `git diff`, `ls | wc -l` before `ls`, `wc -l file` before `cat file`.
6. hetz squeeze runs by default (`HETZ_SQUEEZE=0` turns it off). Add `# hz:raw` to a command to get its raw output. Squeeze's footer also shows the path of the full log.

## Per tool

**RISK** marks flags that hide failure detail. Use them only on runs you expect to pass.

| Tool | Quiet form | Watch out | Source |
|---|---|---|---|
| git | `git status -sb` (or `--porcelain=v1 -b`) · `git log --oneline -n 20` · `git diff --stat` / `--name-only` before a full diff · `git --no-pager …` · `-q` on `fetch`, `pull`, `push`, `clone`, `checkout`, `commit` · `git show --stat <sha>` | `-q` keeps errors (stderr). Avoid `git log -p` without a path and `-n`. | local |
| npm | `npm ci --no-fund --no-audit --no-progress --loglevel=error` · `npm test --silent` (drops npm's banner) | **RISK, mild:** `--silent` also hides npm's own error summary. The runner's output and the exit code survive. | docs |
| pnpm | `pnpm install --reporter=append-only` (or `--reporter=silent`) · `pnpm -s run <script>` | **RISK:** `--reporter=silent` hides install errors too; prefer `append-only` for installs. | docs |
| yarn | Classic: `yarn -s` / `--silent`, `--no-progress`. Berry: `YARN_ENABLE_PROGRESS_BARS=false` | Berry options not locally checked. | docs, M |
| bun | `bun install --silent` or `--no-progress` · `bun test --bail` | **RISK:** `--silent` hides install errors. | docs, M |
| pip | `pip install -q --disable-pip-version-check --progress-bar off --no-color` | `-q` is additive: `-qq` is errors only; **RISK:** `-qqq` hides errors. | local |
| uv | `uv -q …` or `--no-progress` (env `UV_NO_PROGRESS=1`) | `-qq` writes nothing to stdout. | local |
| poetry | `poetry install -q --no-interaction --no-ansi` | | docs, M |
| pytest | `pytest -q --tb=short` · `-x` or `--maxfail=3` · `--no-header` · `-rfE` (one-line failure/error summary) | **RISK:** `--tb=no` hides why tests failed; `--tb=line` keeps only one line. `-x` stops at the first failure, so drop it when you need the full list. `--disable-warnings` hides deprecation warnings that may matter. | local |
| jest | `jest --silent --ci` · `--bail` · `--onlyFailures` (rerun only failed) | **RISK:** `--silent` hides tests' `console.*` output you may need to debug. | docs |
| vitest | `vitest run --reporter=dot` · `--silent=passed-only` (logs only from failing tests) · `--bail=1` · `--hideSkippedTests` | | docs |
| cargo | `cargo build -q` · `cargo test -q -- --quiet` · env `CARGO_TERM_COLOR=never`, `CARGO_TERM_PROGRESS_WHEN=never` | `--message-format=short` drops code snippets from errors. | docs |
| go | `go test ./...` (prints one `ok` line per passing package) · `-failfast` · `go build ./...` (silent on success) · `go vet ./...` | Avoid `-v` except on a single failing test: `go test -run TestX -v ./pkg`. | docs |
| gradle | `./gradlew build --console=plain --warning-mode=summary` · `-q` | **RISK:** `-q` is errors only and cuts test-failure detail. Rerun the failing test with `--tests 'X' -i`, or read `build/test-results/`. | docs |
| maven | `mvn -B -ntp …` (batch mode, no transfer progress) | **RISK:** `-q` hides the Surefire summary; failure details are in `target/surefire-reports/`. | docs |
| make | `make -s --no-print-directory` | Hides command echo only; errors still print. | local |
| docker | `set -o pipefail; docker build --progress=plain . 2>&1 \| tail -60` · `docker pull -q` · `docker compose up -d --quiet-pull --quiet-build` · `docker ps --format '{{.Names}}\t{{.Status}}'` · `docker logs --tail 50 --since 10m <c>` · env `DOCKER_CLI_HINTS=false` | **RISK:** `docker build -q` prints only the image ID, and on failure hides the cause. | local |
| kubectl | `kubectl get … -o name` or `--no-headers` or `-o jsonpath='{…}'` · `kubectl logs <pod> --tail=50 --since=10m --limit-bytes=20000` · `kubectl get events --sort-by=.lastTimestamp \| tail -20` | `-o name` drops status columns; add `-o wide` only when needed. | local |
| terraform | `terraform plan -input=false -no-color -compact-warnings -out=tfplan > plan.log 2>&1; echo exit=$?; grep -E '^(Plan:\|Error\|Warning)' plan.log` · env `TF_IN_AUTOMATION=1` | With `-detailed-exitcode`, exit 2 means "changes present", not failure. | docs |
| brew | `brew install -q …` · env `HOMEBREW_NO_ENV_HINTS=1`, `HOMEBREW_NO_INSTALL_CLEANUP=1`, `HOMEBREW_NO_COLOR=1` | `HOMEBREW_NO_AUTO_UPDATE=1` is quieter but can install stale versions. | local |
| curl | `curl -sS --fail-with-body -o out.json -w '%{http_code}\n' URL` · `--no-progress-meter` | **RISK:** a bare `-s` hides errors (always pair it with `-S`). `-f` alone drops the error body; prefer `--fail-with-body`. | local |
| any long run | `cmd > "$TMPDIR/x.log" 2>&1; echo exit=$?; tail -40 "$TMPDIR/x.log"`, or hand it to the `hetz-crew:runner` agent | | |

## What hetz already sets for you

The session-guard hook writes these to `CLAUDE_ENV_FILE` at session start, so they reach Claude's Bash only and never the Claude Code UI. Variables you have already set are left alone. The authoritative list is in `plugins/hetz/hooks/guard.py`. Turn them off with `HETZ_QUIET_ENV=off`.

- **Default:** `NO_COLOR=1`, `PY_COLORS=0`, `CARGO_TERM_COLOR=never`, `CARGO_TERM_PROGRESS_WHEN=never`, `PAGER=cat`, `GIT_PAGER=cat`, `NPM_CONFIG_FUND=false`, `NPM_CONFIG_PROGRESS=false`, `NPM_CONFIG_UPDATE_NOTIFIER=false`, `PIP_DISABLE_PIP_VERSION_CHECK=1`, `PIP_PROGRESS_BAR=off`, `UV_NO_PROGRESS=1`, `HOMEBREW_NO_ENV_HINTS=1`, `DOCKER_CLI_HINTS=false`, `TF_IN_AUTOMATION=1`, `NEXT_TELEMETRY_DISABLED=1`, `DOTNET_NOLOGO=1`.
- **Not set:** `FORCE_COLOR` (any value, even `0`, makes rich treat the pipe as a terminal and animate progress bars into the output).
- **`HETZ_QUIET_ENV=aggressive` adds:** `CI=1`, `PYTEST_ADDOPTS='-q -rfE'`, `NPM_CONFIG_AUDIT=false`.
  - **RISK:** `CI=1` changes behaviour: jest stops writing new snapshots, and some tools treat warnings as errors. `PYTEST_ADDOPTS` applies to every pytest run.

## Squeeze footer tips

When squeeze shrinks an output, it appends one tip from [`plugins/hetz/share/quiet-flags.tsv`](../plugins/hetz/share/quiet-flags.tsv) (tool, quieter form, optional caveat). That file is the single source; edit it, not this page. A tip is skipped when the command already carries its flags. Tips are one-liners without context: where a tip's flag has a **RISK** mark in the per-tool table above, follow the table.

## Doc sources

npm https://docs.npmjs.com/cli/v11/using-npm/config · pnpm https://pnpm.io/cli/install · jest https://jestjs.io/docs/cli · vitest https://vitest.dev/guide/cli · cargo https://doc.rust-lang.org/cargo/commands/cargo-build.html · go https://pkg.go.dev/cmd/go · gradle https://docs.gradle.org/current/userguide/command_line_interface.html · maven https://maven.apache.org/ref/current/maven-embedder/cli.html · terraform https://developer.hashicorp.com/terraform/cli/commands/plan · poetry https://python-poetry.org/docs/cli/ · bun https://bun.sh/docs/cli/install · Claude Code output limits https://code.claude.com/docs/en/tools-reference
