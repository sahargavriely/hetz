# hetz — token-saving kit for Claude Code

- Layout: marketplace at `.claude-plugin/`; plugins in `plugins/hetz` (hooks + user-invoked skills) and `plugins/hetz-crew` (agents + /delegate).
- Budget rule: `hetz` must add 0 always-on listing tokens (every SKILL.md has `disable-model-invocation: true`); `hetz-crew` ≤ 80. `scripts/check.sh` measures both (`claude plugin details` alone also counts user-only skills).
- Hooks are Python 3 stdlib only, run via `hooks/run.sh`, and must fail open (exit 0, no stdout) on any error. SessionStart/UserPromptSubmit hooks print JSON only, never plain text.
- Never rewrite the user's commands; squeeze only replaces successful output of allowlisted noisy tools and never drops error-like lines.
- Run `scripts/check.sh` before committing (`HETZ_NO_CLI=1` skips the `claude` CLI checks). Tests: no conftest.py; each test file is self-contained.
- Never `@import` anything from `docs/` — docs are for humans, zero runtime cost.
- Dev loop: `claude --plugin-dir plugins/hetz --plugin-dir plugins/hetz-crew`, `/reload-plugins` after edits.
- Usage snapshots (`hetz_ledger.py --json`) go to git-ignored `.claude/ledger/`, never `docs/`: never commit personal usage.
