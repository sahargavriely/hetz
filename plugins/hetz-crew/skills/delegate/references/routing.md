# Routing reference (read only when a step's route is unclear)

## Why 5k tokens
Tokens added to this session are re-read on every later request. A subagent starts cold (~25k tokens Haiku, ~40k Sonnet, ~46k Opus) and returns ~300–800 tokens.
Per MTok, input / cache read / output: Opus 5.5 $4 / $0.20 / $20 · Sonnet 5.5 $2 / $0.10 / $10 · Haiku 5.5 $0.10 / $0.01 / $0.50 (5x above 100k context).
- 50k-token test log inline in an Opus parent, 30 more turns: ~$0.55. Same run via runner: ~$0.02.
- 3k-token task inline: ~$0.08. An Opus subagent's cold start alone: ~$0.23.
- Your prompt to an agent is output at the parent's price: keep it to paths and commands.

## Decision examples
| Step | Route |
|---|---|
| "where is X configured?" (1–2 greps) | inline |
| "map every caller of X across the repo" | Explore, model haiku |
| run the full test suite / build | runner |
| run one fast unit test with short output | inline |
| rename a symbol in 3 named files + run tests | worker |
| choose an API shape, resolve conflicting requirements | inline (judgment) |
| touches auth, payments, migrations, deletes, crypto | worker, then judge regardless of size |
| one-line fix, diff <30 lines, no auth/data/migration | inline edit and inline review |

## Prompt skeletons
- runner: `Run <cmd> [in <dir>]. Acceptance: exit 0. Reply in your RETURN format.`
- worker: `Goal: <one line>. Files: <paths>. Symbols: <names, path:line>. Constraints: <list>. Check: <cmd>. Reply in your RETURN format.`
- judge: `Criteria: <numbered list>. Diff: git diff <base> -- <files>. New files: <paths or none>. Reply in your RETURN format.`
- Explore (model haiku): `Question: <one line>. Scope: <dirs>. Return path:line facts only.`

## Edge cases
- Agent type unavailable (hetz-crew disabled): do the step inline; do not substitute general-purpose.
- Worker returns Open: answer it yourself if the spec decides it; otherwise ask the user, then resend.
- Two workers need the same file: merge into one worker step or run them in sequence.
- No git repo, or no commits yet (`git stash create` and `git diff HEAD` fail): judge gets the file list and Reads the files.
- A worker edited a file that was untracked before: judge Reads it whole.
- Long-running agent tasks keep Haiku under 100k context: split work rather than raise maxTurns.
- Agents set Bash timeout 600000 (the 10 min cap) for long runs; slower suites: split them.

## Alternatives
- Main model is picked at session start; switching mid-session rebuilds the cache.
- On Anthropic API auth, `/advisor opus` with a Sonnet main is the native continuous judge.
