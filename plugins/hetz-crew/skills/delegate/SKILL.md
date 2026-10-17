---
name: delegate
description: Delegate to Haiku/Sonnet agents, Opus judge.
disable-model-invocation: true
argument-hint: <task | .claude/specs/x.md>
allowed-tools:
  - Agent(hetz-crew:runner, hetz-crew:worker, hetz-crew:judge, Explore)
  - Read
  - Bash(git diff*)
  - Bash(git status*)
  - Bash(git stash create)
---
Delegate: $ARGUMENTS

You orchestrate; cheaper agents absorb bulky work. Keep this session's model and effort: never /model or suggest switching mid-session (it rebuilds the cache).

1. INPUT. Empty: ask for the task. A path: Read it as the spec. Open high-rework choices: ask ≤3 questions or suggest /hetz:interview.
2. PLAN. Show one table, then wait for one approval:
   step | route | files | check | est. tokens it would add here
   Route by the raw output a step would add to this context:
   - inline (default): <~5k tokens, ≤3 tool calls, needs this conversation, or a design judgment.
   - Explore, model "haiku": a question needing many reads or searches.
   - hetz-crew:runner: a command whose output may exceed ~5k tokens (tests, builds, lints).
   - hetz-crew:worker: a bounded edit, ≤~5 named files, with a check.
   - hetz-crew:judge: once per change, after the last worker step and after any step touching security, auth, data, migrations or deletes. Diffs <30 lines: review inline, except security/auth/data/migrations/deletes, which always get the judge.
   Never delegate a step adding <~5k tokens: a cold start costs 25–46k. Unsure: read ${CLAUDE_SKILL_DIR}/references/routing.md.
3. PROMPT each Agent call: one-line goal; exact paths and symbols; constraints; a check (worker: the narrowest test of its change); "Reply in your RETURN format." Give paths and commands, never pasted files, logs or diffs.
4. EXECUTE with run_in_background: false. Independent Explore/runner calls in one message (≤4). Workers one at a time unless files are disjoint. Trust returns; re-read only to check a specific claim.
5. VERIFY. Before the first worker run `git status --short` and `git stash create` (prints a snapshot sha, changes nothing); base = that sha, else HEAD, so pre-existing changes are not blamed. Acceptance commands: runner if output may exceed ~5k tokens, else inline; once (skip if the last worker's check was exactly that and passed). Judge gets the criteria, `git diff <base> -- <files>` and new untracked files. On FAIL: defect lines to a worker, rerun the check, re-judge only if the fix diff is ≥30 lines or touches security/auth/data/migrations/deletes (else check inline). Max 2 fix loops, then stop and report.
6. REPORT ≤10 lines: steps, judge verdict, test exit code, agents used, open items. End with: "Next: /hetz:handoff, then /clear."
