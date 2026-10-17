# hetz bench: controlled A/B runs (measurement layer 2b)

This bench measures whether each hetz lever saves billed tokens without losing quality. It runs `claude -p` over fixed task fixtures, once per arm (a set of settings and plugins), repeated k times in a seeded random order. `report.py` then compares every arm with the control arm A.

The bench spends real money, so `run.py` only prints the plan until you pass `--execute`.

```sh
python3 bench/run.py --list                              # arms and tasks
python3 bench/run.py --preset cheap -k 5                 # dry run: plan, worst-case $, every command
python3 bench/run.py --preset cheap -k 5 --execute       # per change: A vs D on repo-qa, noisy-suite, three-tasks
python3 bench/run.py --execute                           # full matrix: monthly, or after a Claude Code upgrade
python3 bench/report.py                                  # the newest results file in bench/results/
python3 bench/report.py --all                            # pool every file there (refuses differing arm definitions)
python3 bench/report.py bench/results/<run_id>.json --json
python3 -m pytest bench/tests -q                         # free: report math, dry-run output, fixtures, fake-claude e2e
```

Run it from a normal terminal, not from inside a Claude Code session. A nested `claude` cannot reuse the host session's auth. The runner aborts after `--max-errors` (default 3) consecutive runs that billed nothing or hit an infra error.

## What one run does

1. `sh tasks/<task>/setup.sh <work>/repo` builds a fresh git fixture. The work dir is `$TMPDIR/hetz-bench/<run_id>/<order>-<arm>-<task>-r<rep>`. A custom `--work-root` must contain `hetz-bench` (see Caveats).
2. The arm's `project_files` are copied into the fixture and added to `.git/info/exclude`.
3. For each turn of `task.md` (with the arm's `prompt_prefix` and `extra_turns` applied), the runner calls:
   `claude -p <turn> --output-format json --setting-sources project,local --settings <work>/settings/<arm>.json --strict-mcp-config [--plugin-dir …] --model <pinned> --effort <pinned> --max-turns <max_turns> --max-budget-usd <budget> --permission-mode acceptEdits --permission-prompts none --allowedTools Bash,Edit,Write --session-id|--resume <uuid>`
   - Stdin is closed.
   - The inherited `HETZ_*`, `CLAUDE_CODE_*` (except auth, provider and proxy vars), model-alias (`ANTHROPIC_DEFAULT_*`, `ANTHROPIC_CUSTOM_MODEL_OPTION*`) and lever vars are removed, so `sonnet`/`haiku` resolve the same for every arm.
   - `DISABLE_AUTOUPDATER=1` is set, and the arm's `env` is added.
4. `sh tasks/<task>/check.sh <repo> <answer.txt>` decides pass or fail (exit 0 = pass). `answer.txt` holds the last turn's `result`.
5. The run is recorded:
   - from the JSON result: `total_cost_usd`, `num_turns`, `usage` and `modelUsage`, summed over the run's calls. On `--resume`, Claude Code 2.1.272 restores the session's cost counters from the transcript's last `cost-state` entry, if there is one (read from the bundle; not yet seen in a live `-p` run). A resumed call then reports session-cumulative cost and `modelUsage`. The runner subtracts that entry, so each call is counted once; `reported_cost_usd` and `restored_cost_usd` stay in the record. When the restore happens, `--max-budget-usd` also caps the session as a whole.
   - from the transcripts, `~/.claude/projects/*/<session>.jsonl` and its `subagents/` (read only): peak and median context, request count, cache breaks (a cache write over 20k after the first request), subagent requests, and token sums used to cross-check `modelUsage`.

Isolation:
- `--setting-sources project,local` keeps your user settings, user plugins, `~/.claude/agents` and `~/.claude/CLAUDE.md` out of the run.
- `--strict-mcp-config` keeps your MCP servers out.
- Managed (policy) settings still apply.
- If you authenticate through `apiKeyHelper` in user settings, use `ANTHROPIC_API_KEY` for bench runs instead.

Scheduling:
- Each arm × task cell runs `k + warmup` times.
- The first `warmup` runs of each cell that execute are tagged and dropped by `report.py`.
- The design asks for k ≥ 5 and one warm-up.

Pinning:
- `--model` (default `claude-opus-5-5`), `--effort` (default `medium`) and the Claude Code version are pinned.
- `--execute` refuses to start unless `claude --version` is `2.1.272`; pass `--expect-version ''` to accept any version.
- `DISABLE_AUTOUPDATER` stops the version from changing mid-run.

Results:
- Results go to `bench/results/<run_id>.json`, which is gitignored. The file is rewritten after every run, so partial results survive Ctrl-C.
- It holds the meta (version, pins, seed, hetz git sha), the resolved arms, and one record per run.

## Arms (`arms/*.json`)

| arm | what changes |
|---|---|
| A | control: defaults plus the built-in Concise output style |
| B | A plus the settings profile: every item of `plugins/hetz/profiles/safe.json` except the statusline |
| C | B plus the `plugins/hetz` hooks (squeeze, guard, contract, quiet env), with the switches forced on |
| D | C plus `plugins/hetz-crew` and the Explore-on-Haiku override |
| D-sonnet | D with a Sonnet main model |
| D-no-cap / -squeeze / -contract / -crew / -explore / -quietenv | leave-one-out ablations of D, each with its target tasks |
| D-clear / D-handoff | three-tasks only: a fresh session per turn, without and with `/hetz:handoff` first |
| D-delegate | failing-test and rename only: the task is sent as `/hetz-crew:delegate <task>`, then one approval turn (the skill waits for plan approval) |

D-sonnet, D-clear, D-handoff and D-delegate set `variant_of: D`, so the report compares each with D directly.

Fields:
- `extends`: inherit from another arm.
- `settings`: deep-merged into the parent's settings; a `null` value deletes a key.
- `settings_profile`: `{path, exclude, fill}`, a hetz profile whose items are merged (arrays unioned, `{placeholders}` filled).
- `settings_files`: extra settings JSON files to merge in.
- `remove_settings`: dotted keys to delete.
- `plugin_dirs`: replaces the parent's list.
- `env`: merged with the parent's; values are given to `claude` and its hooks.
- `project_files`: maps a destination to a repo-relative source; `null` drops an entry.
- `model` / `effort`: override the pinned values.
- `rotate`: how multi-turn tasks run: `resume` (one session), `clear` or `handoff`.
- `prompt_prefix`: prepended to the task's first turn; `extra_turns`: turns appended after the task's own.
- `extra_args`: more CLI arguments.
- Not inherited:
  - `only_tasks`
  - `ablates` (component name)
  - `ablation_base` (default `D`)
  - `target_tasks`: the tasks the ablation verdict is judged on
  - `variant_of`: the arm this one is compared with in the Variants table

The Explore override is copied into the fixture as a project agent (`.claude/agents/Explore.md`). Under `--setting-sources project,local`, Claude Code does not load user-scope agents. A project agent outranks the built-in Explore of the same name, as a user agent does.

## Tasks (`tasks/<name>/`)

Each task directory holds:
- `task.md`: the prompt. Turns are separated by a `<!-- turn -->` line.
- `setup.sh [DIR]`: builds the fixture in an empty DIR, or in a mktemp dir if none is given, and prints its path. It is deterministic: pinned git identity and dates, no network.
- `check.sh DIR [ANSWER]`: exit 0 means pass.
- `max_turns`: the `--max-turns` value for each call.

Shared helpers are in `tasks/_lib.sh`. `bench/tests/test_tasks.py` proves three things for every task:
- the unsolved fixture fails its check
- a reference solution passes it
- typical wrong fixes fail it

| task | design # | job | graded on |
|---|---|---|---|
| failing-test | 1 | fix half-cent rounding in an invoicing library | the visible suite, tests/ untouched, and hidden rounding cases (a naive `>=` fix fails) |
| repo-qa | 3 | answer 3 questions about a ~110-file service | the key facts in the answer, with the repo untouched. Distractors: a stale doc, a deprecated module, near-miss constants |
| noisy-suite | 4+5 | `make test`: ~40k chars of coloured build log plus 335 verbose tests, 3 failures from 2 causes | `make test` green, tests/tools/Makefile untouched, hidden cases |
| rename | 6 | rename `calc_total` across 10 files without touching look-alikes | no word `calc_total` left, `calc_total_weight` and `recalc_totals` intact, suite green, no alias |
| three-tasks | 7 | three unrelated turns: a CSV delimiter bug, `greet --version`, and "which files define main()" | all three, including that the version is read from VERSION |

Not covered from the design:
- Task 2 (TypeScript with hidden tests): it would need Node.
- Task 8 and the `/hetz:interview` comparison: they need a scripted multi-turn answerer.
- `/hetz-crew:delegate` vs plain implementation is covered by D-delegate vs D on failing-test and rename, on cost, peak context and pass rate. "Tokens to the first passing verify" and correction turns are not measured.
- The token cross-check runs in-process: the runner sums usage over the main and subagent transcripts (deduped by message id), the same source `hetz_ledger --json` reads, and the report compares it with `modelUsage`. To check one session by hand: `python3 scripts/hetz_ledger.py --session <id> --json`.

## Report (`report.py`)

Input: a results file, or a directory, which means its newest file (run_ids sort by start time). `--all` pools every file in the directory. Pooling refuses an arm whose definition (settings, plugins, env, project files, model, effort, rotation, prompt changes) differs between files, unless `--allow-mixed`. It warns when the files differ in Claude Code version, model, effort or hetz git sha, or include a dirty tree. Unreadable files are skipped with a warning.

Per task × arm, the report shows:
- n and pass count
- median cost and IQR (Q1–Q3)
- the change vs A as a percentage, with a 95% CI from a stratified percentile bootstrap (stdlib `random`, seeded per comparison)
- $/pass
- median turns and median peak context
- excluded runs: infra errors, and runs whose cost is unknown

A second table shows the median tokens per run by type (input, cache read, cache write and output), summed over `modelUsage` so subagents are included. It also shows median context and cache breaks, plus one line comparing transcript tokens with `modelUsage` on the input side (output is under-recorded in transcripts).

Decision rules from docs/DESIGN.md:
- Suite: the sum of per-task medians over tasks shared with A. Gate: at least a 10% cut and non-inferior, meaning at most 1 extra failure per task.
- Components: the base arm (D) is compared with its ablation on the component's target tasks. The verdict is `keep` when the comparison is non-inferior and either cost falls at least 3% or peak context falls at least 20%, with the 95% CI excluding 0. Otherwise the verdict is `not proven: opt-in` or `quality drop`.
- Variants: each `variant_of` arm against its base on the tasks both ran: cost and peak-context deltas with CIs, passes, and a verdict (`cheaper`, `costlier`, `no clear difference` or `quality drop`).

Run errors come in two kinds:
- Infra errors are the bench's fault: setup failures, `claude` not runnable, a result with `is_error` that billed nothing (for example auth errors), and exceptions inside the runner. They are excluded from all statistics and counted per cell.
- Agent errors are timeouts, crashes and unparseable output, which an arm can cause. The run counts as failed, whatever the check says (`check_pass` keeps the check's verdict). Its cost is unknown, so it is left out of cost medians (`cost_known_usd` holds the reported part). It is shown per cell as "no $".

Non-inferiority is judged on every task both arms ran, including tasks where every run of an arm timed out.

## Caveats

- `total_cost_usd` is a list-price estimate, not your subscription bill. Compare relative changes only.
- Costs vary a lot from run to run, so keep k ≥ 5 and pin everything.
- These tasks rarely pass 200k context, so the cap ablation will usually show no effect. Evidence for the cap comes from the field ledger (layer 3).
- Bench sessions are written to `~/.claude/projects/<encoded fixture path>`, and every such dir name contains `-hetz-bench-` (the runner refuses a work root without `hetz-bench`). `scripts/hetz_ledger.py` skips those dirs by default, so bench spend never counts as real work; pass `--project hetz-bench` to see it. Each run record lists its `session_ids` for auditing.
- Plugin hooks write their logs to the plugin data dir as usual.
- Record every published result in docs/EVIDENCE.md with k, model, effort and Claude Code version.
