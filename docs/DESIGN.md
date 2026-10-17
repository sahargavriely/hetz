# PHILOSOPHY
SCORES (1-10 per criterion: savings / self-overhead / quality safety / UX / faithfulness / implementability, then overall):
- minimal-footprint: 8 / 10 / 9 / 7 / 6 / 8, overall 8.0.
  - Strengths: the best evidence discipline and token budget. autoCompactWindow, bashOutputMaxChars and a squeeze that leaves exit codes alone go straight at the measured bill.
  - Weaknesses: the user's inventions are underweighted. Delegation is shrunk to one Haiku grunt and the strong-model judge is dropped.
  - Errors:
    - Quiet-tool vars (NO_COLOR, PYTEST_ADDOPTS) go in settings `env`, which also reaches the Claude Code process itself, not only Bash.
    - It relies on bare-name Read deny rules in user settings. The dossier says user-scope patterns need `//` or `~/` anchoring to apply in every project.
    - It denies lockfiles globally. That blocks Edit, for example when resolving a lockfile merge conflict.
- max-savings: 9 / 6 / 7 / 5 / 8 / 6, overall 6.8.
  - Strengths: the richest lever set. Worth grafting: CLAUDE_ENV_FILE quiet-env, the SubagentStart return contract, the Explore-on-Haiku override, cold-resume and model-switch guards, and the forked judge.
  - Weaknesses: 16 components. A forced ~220-token output style overrides the user's own choice. Stage-2 lossy trimming is on by default.
  - Errors: two unverified claims are stated as fact: that PreModelSwitch accepts `permissionDecision: ask`, and that PostToolUse never fires on a non-zero exit.
- workflow-ux: 6 / 7 / 8 / 8 / 9 / 7, overall 7.5.
  - Strengths: the best pipeline, interview → spec → /clear → delegate → Opus judge at checkpoints. The best setup UX: manifest-exact undo, personal scope by default, session launchers.
  - Weaknesses and errors:
    - It omits autoCompactWindow, the single largest measured lever: 63% of the largest measured session came from requests over 600k context, and 90% from requests over 300k.
    - It puts always-on text in ~/.claude/CLAUDE.md.
    - Its output-coach adds model tokens instead of removing output.
    - It assumes a slash command passed as the initial CLI prompt runs; that is unverified.

SYNTHESIS:
- Base: minimal-footprint (zero-token core plus evidence gates).
- From workflow-ux: the interview → spec → /clear → /hetz:delegate → crew → judge pipeline (the user's two inventions, kept and made concrete), manifest undo, personal-scope deny rules, optional launchers.
- From max-savings: quiet env through CLAUDE_ENV_FILE (Bash only), the SubagentStart return contract, the user-scope Explore override on Haiku, cold-resume and model-switch warnings as systemMessages only, the commit-boundary nudge.
- Final count: 5 user-invoked skills, 3 hook scripts in one plugin, 4 agent definitions, 1 settings profile, a statusline, docs and a measurement harness.

PRINCIPLES:
- Follow the bill.
  - In a measured developer's transcripts, list-price cost splits 68% cache reads, 25% cache writes, about 7% output. Cost ≈ context size × requests + cache rebuilds.
  - Priority order: cap and rotate context; prevent cache busts; keep bulky tool output out of the parent (filter it, or delegate it to a cheap model); settle ambiguity while context is tiny; trim always-loaded text; terse prose last.
- Use the cheapest mechanism that enforces each rule, in this order: setting/env > hook (harness-only, 0 model tokens; systemMessage reaches the user, not Claude) > user-invoked skill (disable-model-invocation, 0 tokens until used) > agent description (paid every request in every repo) > always-on text.
  - Budget for listing tokens: hetz = 0, hetz-crew ≤ 80 (`claude plugin details`, enforced in CI).
  - Accepted standing costs: the first-party Concise style, and about 30 tokens per Explore or general-purpose spawn.
  - No hetz text in ~/.claude/CLAUDE.md.
- Never trade correctness silently.
  - Commands are never rewritten.
  - Filters touch only allowlisted noisy commands, never drop error-like lines, and always save the raw log with its path shown.
  - Every hook fails open.
  - Cheap-model work is checked by a strong-model judge at checkpoints, not at every step.
- Honor the user's ideas, bounded by evidence.
  - Interview keeps probing while high-rework decisions are open. It has recommended answers and an 'enough' exit, and ends in a spec plus a fresh session.
  - Delegation routes by expected output size and difficulty (Haiku legwork, Sonnet bounded edits, Opus judge). It never switches the main model mid-session.
- Everything is reversible and personal by default. Settings change only after a preview, with a backup and a manifest. Per-repo rules go to .claude/settings.local.json unless the user picks the team file.
- Components earn their place. Each has an ablation in the bench and a metric in the ledger. Anything that does not beat baseline at an equal pass rate moves to opt-in or is deleted.


## COMPONENT: core settings profile (profiles/safe.json + profiles/optional.json)
- **kind**: setting
- **invocation**: one-shot setup (applied by /hetz:setup global; reversible)
- **purpose**: Deliver the levers plugins cannot ship (compaction cap, output caps, env knobs, Read deny, outputStyle, statusLine) into ~/.claude/settings.json once, so they apply to every repo at near-zero model-context cost.
- **derivedFrom**: rotate sessions (autoCompactWindow and idle nudge), verbose output (bashOutputMaxChars, MCP cap), ignore (Glob fix and minimal global deny), caveman (built-in Concise), delegation (subagent model). From all three designs; settings env quiet vars moved out to session-hooks.
- **estimatedImpact**: HIGH, the bulk of the savings.
- Cap: 1.5–4x cheaper per request on any session that would have grown past 300k. Expect about 30–60% off long sessions and about 0 on short ones.
- Bash cap: Bash produced 52% of measured tool-result chars (223 of 699 calls over 2k chars).
- Subagent model: up to 2x on general-purpose spawns.
- Glob fix and deny: low–medium.
- Concise: about 1% of the bill, but it is first-party and cached.
- **alwaysOnContextCost**: About 0. Caps, env and anchored deny rules with specifiers do not change tool schemas. Concise is about 100–150 cached system-prompt tokens plus a ~20-token per-turn reminder, partly replacing default style text. Kept only while the bench shows output savings at least that large.
- **risks**: - Compaction loses detail mid-task. Mitigations: the `# Compact instructions` stub that /hetz:slim inserts, plus /hetz:handoff.
- A 12k Bash cap adds round trips when big output is really needed; tune 8k–20k.
- `//**` anchoring and top-level autoCompactWindow on 2.1.272 must pass setup's self-tests.
- It writes global settings, so preview, backup, manifest and undo are mandatory.

### contentOutline
plugins/hetz/profiles/safe.json (pre-checked in setup):
- "autoCompactWindow": 200000
  - Why 200k:
    - 1M-window models (Sonnet 5+, Opus 4.7+, Haiku 5.5) otherwise compact only at ~967k.
    - 200k keeps requests under long-context price tiers (JetBrains' arm went +11.6% by crossing one).
    - It roughly bounds the 90%-of-cost >300k tail.
  - Verify after applying: /context shows the window. If it is not honored on 2.1.272, setup writes env.CLAUDE_CODE_AUTO_COMPACT_WINDOW="200000" instead.
  - Bench-tune within 150k–300k.
- "bashOutputMaxChars": 12000 (default 30000; range 4k–128k; needs v2.1.261+). Overflow goes to a file plus a 2k preview, which Claude greps.
- "outputStyle": "Concise", set only if the user has no outputStyle.
- "env":
  - CLAUDE_CODE_GLOB_NO_IGNORE="false": Glob starts respecting .gitignore.
  - CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION="false": removes one background request per turn.
  - CLAUDE_CODE_IDLE_THRESHOLD_MINUTES: "60" on subscription, "5" when ANTHROPIC_API_KEY or apiKeyHelper is detected. Matches the cache TTL.
  - CLAUDE_CODE_IDLE_TOKEN_THRESHOLD="50000". This tunes the native 'new task? /clear to save N tokens' nudge.
  - CLAUDE_CODE_SUBAGENT_MODEL="sonnet": general-purpose subagents stop inheriting Opus.
  - MAX_MCP_OUTPUT_TOKENS="12000" (default 25000).
- "permissions": {"deny": ["Read(//**/*.min.js)", "Read(//**/*.min.css)", "Read(//**/*.js.map)", "Read(//**/*.css.map)"]}
  - Patterns are anchored with `//` because user scope needs it.
  - Lockfiles and build dirs are NOT denied globally, because Read deny also blocks Edit/Write. Per-repo candidates come from /hetz:slim.
- "statusLine": {"type":"command","command":"~/.claude/hetz/statusline.py"}, only if none is set.

plugins/hetz/profiles/optional.json (unchecked; each item shown with its trade-off):
- "model": "sonnet": 2x cheaper per token than the Opus 5.5 default. Opus stays one launcher away.
- "advisorModel": "opus": shown only on Anthropic API auth.
- env.CLAUDE_CODE_GOAL_CHECKIN_MINUTES="0"
- env.CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS="12000"

Deliberately absent:
- MAX_THINKING_TOKENS: ignored on the 5.5 models.
- DISABLE_NON_ESSENTIAL_MODEL_CALLS: not in 2.1.272.
- CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: also kills updates and the advisor.
- CI=1: changes tool behavior, e.g. jest snapshots.
- NO_COLOR and npm/pip vars in settings env: these go in session-hooks so they reach only Bash.

## COMPONENT: /hetz:setup (scripts/hetz_settings.py + scripts/install.sh + scripts/uninstall.sh)
- **kind**: skill
- **invocation**: user: /hetz:setup [global|repo|status|undo [global|repo]] (disable-model-invocation: true). Bootstrap from the shell: ~/Projects/hetz/scripts/install.sh
- **purpose**: One safe, reversible path from 'cloned repo' to 'active in every repo', covering what plugin manifests cannot set.
- **derivedFrom**: workflow-ux /hetz:setup (manifest undo, personal scope, conflict detection, launchers) + minimal-footprint setup.sh (diff, backup, idempotence) + max-savings setup (profiles, Explore override)
- **estimatedImpact**: Enabler. Without it the cap, output caps, Explore override and statusline (most of the high-impact items) never get applied.
- **alwaysOnContextCost**: 0 (disable-model-invocation).
- **risks**: - Persistent config edits: mitigated by dry-run, backup, manifest-exact undo and personal scope.
- Concurrent edits by `rtk init`: re-read the file right before writing.
- python3 missing: the skill fails with a clear message, and hooks fail open.

### contentOutline
scripts/install.sh (bash; idempotent):
1. Warn if `claude --version` is below 2.1.272.
2. `claude plugin marketplace add "$REPO_DIR"`. A directory source loads in place.
3. `claude plugin install hetz@hetz` and `claude plugin install hetz-crew@hetz` (user scope).
4. Write `$(command -v python3)`, resolved through pyenv to the real interpreter path, into ~/.claude/hetz/py. Hooks use it to skip shim latency.
5. Print 'Open any session and run /hetz:setup global'.

plugins/hetz/skills/setup/SKILL.md frontmatter:
- name: setup
- description: Configure hetz for this machine (global) or this repo, with preview and undo.
- disable-model-invocation: true
- argument-hint: [global|repo|status|undo]
- allowed-tools: Bash(python3 *hetz_settings.py *), AskUserQuestion, Read

Body steps (≤100 lines; the model never hand-edits JSON):
1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hetz_settings.py" status --json`. It reports current user and project settings, the manifest at ~/.claude/hetz/manifest.json, the Claude version, auth type, and conflicts: rtk hook present, caveman plugin enabled, existing statusLine or outputStyle.
2. GLOBAL: one AskUserQuestion multiSelect.
   - safe.json groups, pre-marked (Recommended): compaction cap; output caps; env knobs; Glob fix + minimal deny; Concise; statusline.
   - optional.json items.
   - Explore override: write ~/.claude/agents/Explore.md from templates/Explore.md.
   - Fish launchers: write ~/.config/fish/functions/cc*.fish after a name-collision check. Zsh/bash equivalents if that's the login shell.
   - Conflict notes:
     - caveman enabled: suggest disabling it, since it costs ~1.2k tokens plus a per-turn reminder.
     - rtk hook found: squeeze skips rtk commands; suggest exclude_commands for git diff, cat and ls; never run both on the same command.
3. Run `hetz_settings.py apply --scope user --items <ids> --dry-run`, show the key-level unified diff, and confirm once.
4. Apply:
   - Back up to settings.json.hetz-<ts>.bak.
   - Deep merge: arrays unioned, env keys only if absent. A differing existing scalar is asked about, never overwritten silently.
   - Record each key with its prior value, plus copied files, in the manifest.
   - Copy plugins/hetz/scripts/statusline.py to ~/.claude/hetz/statusline.py. The plugin root changes on GitHub-installed updates.
5. Self-tests, printed as instructions for the next session:
   - 'ask Claude to Read some file.min.js → expect denied'
   - '/context shows the 200k compact window'
   - '/agents shows Explore (user) on haiku'
6. End with: 'Takes effect in new sessions.'

REPO mode: runs the slim audit's deny scan only (see /hetz:slim) and writes chosen rules to .claude/settings.local.json by default, or .claude/settings.json on request.

STATUS prints the manifest. UNDO restores the exact prior values and removes copied files. scripts/uninstall.sh does the same without Claude, then runs `claude plugin uninstall` and `claude plugin marketplace remove hetz`.

Launchers (optional; fish shown):
- cc → claude --model sonnet
- cc-deep → claude --model opus --effort high
- cc-quick → claude --model haiku --effort low
- cc-spec → claude --model opus "/hetz:interview $argv"
- cc-do → claude --model sonnet "/hetz-crew:delegate $argv"
- Ship the slash-command launchers only after verifying on 2.1.272 that an initial-prompt slash command executes. Otherwise print the command to paste.

## COMPONENT: squeeze (PostToolUse Bash output filter)
- **kind**: hook
- **invocation**: always-on (plugin hetz; hooks/hooks.json PostToolUse, matcher "Bash")
- **purpose**: RTK's useful part with no binary and no command rewriting. It shrinks noisy successful output from install, build and test tools before it enters context, where it would be re-read on every later request. It also teaches quiet flags at the moment of waste.
- **derivedFrom**: RTK + verbose terminal output / quiet flags / custom filters (merged). Base from minimal-footprint; max-savings' lossless stage-1, `# hz:raw` escape and TSV ledger; workflow-ux's coach folded into a footer tip.
- **estimatedImpact**: MEDIUM on dev-tool-heavy repos: typically 70–95% fewer chars on install, build and passing-suite calls, re-saved on every later request. LOW in research-style sessions, where large outputs were mostly cat/sed/grep dumps; those are left to the 12k cap. It ships enabled only after the offline replay gate passes; until then HETZ_SQUEEZE=0 is the default.
- **alwaysOnContextCost**: 0 standing (harness-only). About 25 footer tokens, only on calls where it removed ≥30% of a ≥2k-char output, so it is always net-negative.
- **risks**: - Unverified on 2.1.272:
  - whether PostToolUse fires for non-zero exits (expected: PostToolUseFailure, not hooked) — verify with a failing fixture; if it does fire, KEEP lines still survive
  - whether tool_response holds the full or the already-capped output
  - whether the updatedToolOutput Bash shape is exact (a wrong shape is silently ignored)
- Lossy trimming can mislead. Mitigations: KEEP regex, raw-log path, `# hz:raw`.
- About 30–60 ms per Bash call.
- Never stack with RTK, tokf or context-mode on the same command.

### contentOutline
hooks.json entry: {"matcher":"Bash","hooks":[{"type":"command","command":"P=$(cat ~/.claude/hetz/py 2>/dev/null || command -v python3) || exit 0; exec \"$P\" -I -S \"${CLAUDE_PLUGIN_ROOT}/hooks/squeeze.py\"","timeout":5}]}

hooks/squeeze.py: stdlib only. Any exception → exit 0 with empty stdout.

Pass through (print nothing) when any of these holds:
- HETZ_SQUEEZE=0
- tool_response.interrupted or isImage
- len(stdout)+len(stderr) < 2000
- the command contains `# hz:raw`
- after stripping leading `VAR=x`, `cd … &&`, `time`, `npx`, `pnpm exec`, `uv run` and `poetry run`, the first word is a content reader: cat head tail sed awk less bat nl jq yq grep rg ag find fd diff wc echo printf rtk
- a git content subcommand: diff show blame cat-file, or `log -p`
- the first word is not on the NOISY allowlist:
  - npm/pnpm/yarn/bun (install, ci, add, build, test, run)
  - pip/uv/poetry (install, sync, add)
  - brew, apt
  - docker (build, pull, compose)
  - cargo (build, check, test)
  - go (build, test, mod)
  - make, cmake, gradle, ./gradlew, mvn, ./mvnw
  - pytest, jest, vitest, tsc, eslint
  - terraform (init, plan)
  - bundle, composer

Pipeline, run on stdout and stderr separately:
- Stage 1 (lossless-ish, always):
  - strip ANSI/OSC
  - keep only the last segment of \r-overwritten lines
  - drop spinner and progress lines
  - collapse ≥3 identical lines to one line + '(×N)'
  - collapse >5 consecutive lines that share a 20-char prefix, or differ only in digits/hashes, to first 2 + '… N similar lines'
  - drop per-tool noise:
    - npm: 'WARN deprecated', notice, 'added N packages'
    - pip: Collecting/Downloading/'Requirement already satisfied'/'Using cached'
    - cargo: Compiling/Checking/Downloaded/Fresh
    - docker: '#N sha256', extracting, CACHED, DONE
    - gradle: '> Task … UP-TO-DATE/NO-SOURCE'
    - mvn: '[INFO] Download'
    - jest/vitest: PASS and ✓ lines
    - pytest: dot rows
- KEEP regex (case-insensitive): error|fail|warn|exception|traceback|panic|denied|cannot|not found|assert|expected|exit code|✗|✕. KEEP lines, plus 1 line of context on each side, are never dropped.
- Stage 2, only if still >80 lines: first 30 + all KEEP lines (max 40) + last 40.
- If the total reduction is under 30%, pass through.

On a rewrite:
1. Write the raw output to ${CLAUDE_PLUGIN_DATA}/logs/<session_id>/<tool_use_id>.log.
2. Deep-copy tool_response and replace only stdout/stderr, keeping stderr verbatim if under 2k.
3. Append the footer: '[hetz squeeze 1840→62 lines · raw: <path> (grep it) · quieter: <tip>]'.
   - Tips from share/quiet-flags.tsv:
     - npm --silent --no-fund --no-audit
     - pytest -q --tb=short -x
     - cargo -q
     - vitest --reporter=dot
     - jest --silent
     - gradle -q --console=plain
     - mvn -B -ntp
     - docker build --progress=plain | tail with set -o pipefail
     - generic: `cmd > "$TMPDIR/x.log" 2>&1; echo exit=$?; tail -40 "$TMPDIR/x.log"`
   - Annotate flags that hide failure detail (gradle/mvn -q).
4. Emit {"hookSpecificOutput":{"hookEventName":"PostToolUse","updatedToolOutput":{…same shape…}}}.
5. Append a TSV row (ts, session, cmd head, chars_in, chars_out) to ${CLAUDE_PLUGIN_DATA}/squeeze.tsv.
6. If the command matched `git commit` and succeeded, touch ${CLAUDE_PLUGIN_DATA}/state/<session_id>.commit (used by session-guard).

The hook also fires inside subagents, shrinking runner and worker contexts too.

Tests: hooks/tests/test_squeeze.py with golden fixtures for npm i, passing pytest -v, cargo build, docker build, a failing jest run, and git diff (must be untouched). Assert:
- no KEEP line is dropped
- the output shape matches
- p95 latency under 100 ms

## COMPONENT: session-guard (SessionStart + UserPromptSubmit + PreModelSwitch)
- **kind**: hook
- **invocation**: always-on (plugin hetz). hooks.json: SessionStart matcher "startup|resume|clear|compact"; UserPromptSubmit (no matcher); PreModelSwitch (no matcher). All run hooks/guard.py, dispatched on hook_event_name; timeout 5.
- **purpose**: Make 'rotate sessions' and cache hygiene happen at the right moment. It quiets tools at the source and carries handoff notes forward, spending zero model tokens except a one-time handoff pointer.
- **derivedFrom**: rotate sessions + quiet flags. max-savings' quiet-env (CLAUDE_ENV_FILE), meter, cache-guard and handoff pointer; workflow-ux's context-guard and pickup TTL. The commit-boundary nudge is NEW from max-savings.
- **estimatedImpact**: HIGH per incident.
- A cold resume or model switch at 150k on Opus 5.5 costs about $0.75–1.20, versus about $0.03 for a warm turn. Cache writes are 25% of the user's spend.
- The nudge makes the free /clear a visible decision before the cap is reached.
- Quiet env: low–medium, near-zero risk; it also prevents pager hangs.
- **alwaysOnContextCost**: 0 for the quiet env, nudges and warnings (systemMessage is user-only). The handoff pointer is about 40 tokens once, only when a fresh unconsumed note exists.
- **risks**: - Unverified: PreModelSwitch presence and the SessionStart cost fields on 2.1.272 (`claude plugin validate` plus a debug-file run). If PreModelSwitch is absent, drop (e) via a hooks.json variant.
- The transcript format may change; parse defensively and stay silent on errors.
- Nagging, mitigated by the thresholds and the cap of 3.
- Prices are list prices, labelled '~'.

### contentOutline
guard.py (stdlib; fail-open). It must never print plain stdout on SessionStart or UserPromptSubmit, because that would enter context. Output is JSON only.

(a) SessionStart, all sources: quiet env.
- For each var not already set in the environment, append `export K=V` to $CLAUDE_ENV_FILE. The vars reach Bash only, not the Claude Code UI.
- Vars: NO_COLOR=1, FORCE_COLOR=0, PY_COLORS=0, CARGO_TERM_COLOR=never, CARGO_TERM_PROGRESS_WHEN=never, PAGER=cat, GIT_PAGER=cat, NPM_CONFIG_FUND=false, NPM_CONFIG_PROGRESS=false, NPM_CONFIG_UPDATE_NOTIFIER=false, PIP_DISABLE_PIP_VERSION_CHECK=1, PIP_PROGRESS_BAR=off, UV_NO_PROGRESS=1, HOMEBREW_NO_ENV_HINTS=1, DOCKER_CLI_HINTS=false, TF_IN_AUTOMATION=1, NEXT_TELEMETRY_DISABLED=1, DOTNET_NOLOGO=1.
- HETZ_QUIET_ENV=aggressive adds CI=1, PYTEST_ADDOPTS='-q -rfE', NPM_CONFIG_AUDIT=false.
- HETZ_QUIET_ENV=off disables it.
- Also prune log and state files older than 7 days.

(b) SessionStart, source startup|clear: handoff pickup.
- Fires if ./.claude/handoff.md exists, is <12 h old, and its hash is not in state/consumed.
- Emits {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"A handoff note from an earlier session in this repo is at .claude/handoff.md (written 25 min ago; goal: <its first line>). It applies only if the user continues that work."}}
- Then records the hash as consumed, so the pointer is injected at most once.

(c) SessionStart, source resume: cold-resume warning.
- If the input has prompt_cache_likely_expired == true and context_tokens ≥ 60000, emit {"systemMessage":"hetz: resuming ~182k tokens with a cold cache; first request re-writes it (~$<estimated_cache_write_usd>). If you only need conclusions: /clear and continue from .claude/handoff.md."}
- If those fields are absent, fall back to the transcript mtime vs the TTL (60 min on subscription, 5 on API key).

(d) UserPromptSubmit: size and boundary nudge.
- Read the last 256 KB of transcript_path, take the last assistant message.usage, and compute ctx = input + cache_read + cache_creation.
- State lives in ${CLAUDE_PLUGIN_DATA}/state/<session_id>.json.
- Emit only {"systemMessage": …} when either:
  - ctx first crosses 100k or 150k, once each: 'hetz: context 152k — every request re-sends it (~$0.03/req on Opus 5.5). New task → /hetz:handoff then /clear (free). Same task → /compact <focus> now while the cache is warm.'
  - the .commit flag exists and ctx > 40k: 'Task boundary (committed). If this prompt starts a new task, /clear drops ~120k → ~5k per request.' Then delete the flag.
- Throttle to at most 3 messages per session.

(e) PreModelSwitch:
- If ctx ≥ 30k, emit a systemMessage: 'Switching to <to_model> re-sends ~142k tokens uncached (~$0.71–1.14 on Opus 5.5); the old cache is wasted. Cheaper: /hetz:handoff → /clear → start the new model fresh (cc-deep / cc).'
- It never blocks or asks, because the ask decision shape is unverified.
- Prices come from hooks/prices.json (Opus/Sonnet/Haiku 5.5 plus older).

HETZ_GUARD=0 silences (c), (d) and (e).

## COMPONENT: return-contract (SubagentStart)
- **kind**: hook
- **invocation**: always-on (plugin hetz). hooks.json: SubagentStart, matcher "Explore|general-purpose"
- **purpose**: Shrink what flows back into the parent context, where it is re-read on every later request. Output styles don't reach subagents, so caveman's brevity pays off most here.
- **derivedFrom**: caveman (cavecrew insight) via max-savings' SubagentStart hook. Scoped to built-ins so crew agents keep their own contracts.
- **estimatedImpact**: LOW–MEDIUM. Typical subagent summaries of 1–3k tokens fall to about 0.5–1k, re-read on every later main request. The effect is input-side, so it compounds.
- **alwaysOnContextCost**: About 35 tokens per Explore or general-purpose spawn (subagent context only, not the main thread).
- **risks**: Over-compressed summaries cause a re-search; the bench tracks the re-search rate. If the main thread starts re-reading files the subagent already summarized, raise the limit to 40 lines.

### contentOutline
hooks/contract.py (or an inline echo) emits {"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"Return format for the parent: result first; evidence as path:line one-liners; at most 25 lines; no restating the task; no full file contents or raw logs; say what was not checked."}}

- Exact-list matcher only, so Plan, crew agents and third-party plugin agents (which may need structured output) are untouched.
- HETZ_CONTRACT=0 disables it.

## COMPONENT: hetz statusline
- **kind**: script
- **invocation**: always-on UI (statusLine set by /hetz:setup only if none exists; otherwise a wrapper appends the segment)
- **purpose**: A zero-token, always-visible meter that makes rotate, compact and cache decisions obvious.
- **derivedFrom**: rotate sessions; all three designs
- **estimatedImpact**: Indirect but high leverage. Skills and hooks cannot run /clear, so the user's own decision is the rotate mechanism, and this keeps it informed.
- **alwaysOnContextCost**: 0
- **risks**: Statusline JSON fields vary by version: missing fields degrade to '?'. A user's existing statusline is never replaced without asking.

### contentOutline
~/.claude/hetz/statusline.py, copied from plugins/hetz/scripts/statusline.py. Stdlib; every field read with .get(); under 50 ms; never errors.

Output line: `sonnet·med │ ctx 142k/200k │ cache 96% warm │ $3.12`
- ctx = current_usage input + cache_read + cache_creation, in absolute tokens. Percent misleads on 1M windows.
- Denominator: the autoCompactWindow from settings.
- cache% comes from the prompt_cache object (v2.1.251+), else read / (read + creation + input). COLD when the cache is not warm.
- $ comes from cost.total_cost_usd.
- Colors: dim <80k, yellow 80–150k, red >150k with the suffix '→ /hetz:handoff + /clear'. The cache segment turns red below 80% after the 3rd request.
- On bad input it prints the model name only.

Wrapper mode: run the user's existing command, then append ' │ ' + the hetz segment.

## COMPONENT: /hetz:interview
- **kind**: skill
- **invocation**: user: /hetz:interview <idea or issue> (or /interview when the name is unique; launcher cc-spec). disable-model-invocation: true
- **purpose**: The user's invention, kept as the front of the pipeline. It settles every decision that would change the implementation while context is tiny, then hands a dense spec to a fresh session.
- **derivedFrom**: interview (user) + spec-first + rotate. Workflow-ux's 'probe while high-rework nodes are open' honors 'keep probing'; minimal's facts-first and triage skip; mattpocock grilling (recommended answers, facts are Claude's job); Superpowers brainstorming (skip when clear); official interview → SPEC.md → fresh-session practice.
- **estimatedImpact**: HIGH on avoided rework. The evidence is qualitative (official best practice; 'vague requests trigger broad scanning'). Questions at near-zero context cost cents, while a wrong multi-file implementation costs dollars. The fresh session starts at spec size (~1k tokens) instead of carrying the Q&A.
- **alwaysOnContextCost**: 0 (no description in context). About 400 tokens on invoke, discarded by the /clear that follows.
- **risks**: - Interview fatigue: mitigated by triage, recommended answers, ≤4 per round, and the round-3 checkpoint with 'enough'.
- Haiku Explore facts may be wrong; the spec cites path:line so they can be checked.
- The user must actually /clear; the statusline and nudge reinforce it.

### contentOutline
plugins/hetz/skills/interview/SKILL.md frontmatter:
- name: interview
- description: Interview me until the task is fully specified, then write a spec for a fresh session.
- disable-model-invocation: true
- argument-hint: <idea or issue>
- allowed-tools: AskUserQuestion, Read, Grep, Glob, Agent(Explore), Write(.claude/specs/**)
- NO model/effort keys: a different model in a main-thread skill busts the cache.

Body (~400 tokens):
0. TRIAGE. If goal, scope, interfaces and acceptance checks are already explicit in $ARGUMENTS plus the repo, say 'spec complete' and go to step 4.
1. FACTS FIRST. Never ask what the repo, git log or docs can answer. Dispatch 1–3 Explore subagents in parallel for existing patterns, affected files (path:line), test setup and conventions.
2. DECISION TREE. Walk these nodes:
   - goal and user-visible outcome
   - scope and non-goals
   - interface (API/CLI/UI shape)
   - data and migrations
   - edge cases and error behaviour
   - performance and security
   - compatibility
   - acceptance tests
   - rollout
   Rank each open node by the rework a wrong guess would cause.
3. ROUNDS via AskUserQuestion:
   - ≤4 questions per round, highest rework risk first, parents before dependents.
   - Each question: the recommended option first, marked '(Recommended)' with a one-line reason, plus 2–3 alternatives. multiSelect where it fits.
   - Keep going while any high-rework node is open. After round 3, ask 'Continue probing or accept my defaults for the rest?'
   - Low-impact nodes are never asked; they get defaults, listed under 'Defaults I chose'.
   - The last round always includes 'Anything I haven't asked that would change the design?'
   - 'enough' or 'go' at any time accepts the defaults for everything still open.
4. SPEC. Write .claude/specs/<slug>.md, ≤80 lines:
   - Goal · Non-goals · Decisions (Q → answer, tagged user|default) · Interfaces
   - Acceptance checks (runnable commands)
   - Files likely touched (path:line) · Risks/unknowns
   - Plan: numbered steps, each tagged inline|Explore|runner|worker|judge
   - No code beyond signatures.
   - Add .claude/specs/ to .git/info/exclude unless the repo already tracks it.
5. CLOSE with exactly: 'Spec: <path>. Fresh start: /clear (pick the model now), then `/hetz-crew:delegate <path>` — or `implement <path>` for small specs.' Offer to continue in this session only if the spec is ≤15 lines.

No implementation inside this skill.

Optional references/question-bank.md: high-value questions per node, read only when needed.

## COMPONENT: /hetz-crew:delegate
- **kind**: skill
- **invocation**: user: /hetz-crew:delegate <task | .claude/specs/x.md> (launcher cc-do). disable-model-invocation: true. Lives in plugin hetz-crew, so disabling crew removes it.
- **purpose**: The user's 'match models to tasks and effort, let a strong model judge' as an explicit orchestration protocol from the main session, without ever switching the main model.
- **derivedFrom**: delegation (user), from workflow-ux's /delegate, corrected by the evidence: delegation pays only for bulky-output or read-heavy work on cheaper models. Judge-at-checkpoint from max-savings' review. /advisor noted as the native API-only alternative.
- **estimatedImpact**: HIGH for multi-step tasks with verbose verification: the main context stays small and most tokens run on Haiku or Sonnet. Total tokens can still rise, so the bench decides whether it is recommended over plain implementation for each task class.
- **alwaysOnContextCost**: 0 for the skill. The agents it uses are counted in hetz-crew.
- **risks**: - Orchestration overhead on small tasks: limited by the inline default and the 5k rule.
- Cheap-model quality gaps: limited by the judge gate and the 2-loop cap.
- Edit conflicts: workers run in sequence.
- Whether plugin agent names are accepted in skill allowed-tools Agent(...) needs a quick check; if not, drop allowed-tools and rely on prompts.

### contentOutline
Frontmatter:
- name: delegate
- description: Route a spec's steps to Haiku/Sonnet agents and gate the result with an Opus judge.
- disable-model-invocation: true
- argument-hint: <task or spec path>
- allowed-tools: Agent(hetz-crew:runner, hetz-crew:worker, hetz-crew:judge, Explore), Read, Bash(git diff*), Bash(git status*)
- No model/effort keys.

Body (~450 tokens; routing table in references/routing.md):
1. INPUT. Load the spec. For a bare task, ask ≤3 clarifying questions or suggest /hetz:interview first.
2. PLAN TABLE, shown once for one approval: step · tier · agent · files · acceptance check · expected output size.
   Routing:
   - inline: ≤3 tool calls, needs conversation context, or a design judgment
   - Explore (Haiku via override): the answer needs >3 file reads or searches
   - runner: any command expected to print >2k chars
   - worker: a bounded edit with explicit files (≤~5) and a check
   - judge: only after the last worker step, or after any step touching security, data or migrations; diffs <30 lines are reviewed inline
   - Never delegate when the expected output is under ~5k tokens: cold start is 24.5k (Haiku 5.5) to 46k (Opus 5.5).
3. PROMPT CONTRACT for every Agent call: a one-line goal, exact paths and symbols (no rediscovery), constraints, the acceptance command, and a reminder of the agent's return format.
4. EXECUTE. Independent Explore/runner calls in parallel (≤4). Workers in sequence when their files overlap.
5. VERIFY. The runner runs the acceptance commands, then the judge reviews `git diff` against the criteria. On FAIL, send the defects back to the worker, at most 2 loops, then stop and report.
6. REPORT in ≤10 lines: steps, judge verdict, test exit code, agents used. Then: '/hetz:handoff then /clear before the next task.'

Notes in the body:
- Never /model mid-session; pick the main model at start (cc / cc-deep).
- On Anthropic API auth, `/advisor opus` with a Sonnet main is the native continuous judge.

## COMPONENT: hetz-crew agents (runner, worker, judge)
- **kind**: subagent
- **invocation**: auto (Claude may pick runner on its own from the description) and explicit via /hetz-crew:delegate. The judge's description restricts it to checkpoints.
- **purpose**: Match models to tasks. Haiku absorbs verbose runs, Sonnet makes bounded edits, Opus judges once per change. Every agent returns a compact contract, so the parent stays small.
- **derivedFrom**: delegation (user) + verbose output (runner) + caveman's cavecrew return contracts. minimal's grunt, max's runner/worker, workflow-ux's judge. Scout replaced by the Explore override.
- **estimatedImpact**: HIGH where used for verbose or read-heavy work. A 50k-token test log in an Opus 5.5 parent costs about $0.55 over 30 later turns; a runner costs about $0.01–0.02 and returns ~300–800 tokens. Worker: about 2x cheaper per token than Opus. Judge: about $0.25–0.50 per checkpoint, which buys a safe Sonnet main.
- **alwaysOnContextCost**: About 60–75 tokens per request (3 short descriptions) in every repo while hetz-crew is enabled. It is the only listing cost in the kit, isolated in its own plugin for ablation (`claude plugin disable hetz-crew@hetz`).
- **risks**: - Haiku misreads complex failures: mitigated by verbatim error lines and parent or judge review.
- Over-delegation on small tasks raises total tokens; the bench checks that runner is not chosen for one-line questions.
- Judge auto-invocation would be expensive; a trigger eval asserts it never fires unprompted.
- Haiku's subagent cache TTL is 5 min.

### contentOutline
plugins/hetz-crew/agents/runner.md
- Frontmatter: name: runner; description: "Haiku runner for tests, builds, linters, installs or log triage; returns only verdict and failures. Use when output would exceed ~2k chars."; model: haiku; effort: low; tools: Bash, Read, Grep, Glob; maxTurns: 15; color: green. Keeps CLAUDE.md, which holds the project's test commands.
- Body:
  - Never edit files.
  - Always `set -o pipefail`.
  - Run `cmd > "${TMPDIR:-/tmp}/hetz-run-$(date +%s).log" 2>&1; echo exit=$?`, then grep or tail.
  - Use quiet flags.
  - Rerun one failing test once, only to separate flaky from real.
  - Stop at 15 files read.
- RETURN (≤25 lines):
  - `RESULT: pass|fail (exit N): <cmd>`
  - `FAILURES:` ≤10 items, each test id, file:line and ≤3 verbatim decisive error lines
  - `NOTES:` ≤3 lines
  - `LOG: <path>`

plugins/hetz-crew/agents/worker.md
- Frontmatter: name: worker; description: "Sonnet implementer for a bounded, fully specified change. Give files, constraints and an acceptance check."; model: sonnet; effort: medium; tools: Read, Edit, Write, Grep, Glob, Bash; maxTurns: 40; color: blue.
- Body:
  - Touch only the listed files; report any extra ones.
  - No drive-by refactors.
  - Make the smallest change that satisfies the spec.
  - Run the check quietly.
- RETURN (≤12 lines): `Changed:` path (+/−) each · `Check:` cmd → exit · `Deviations:` · `Open:`

plugins/hetz-crew/agents/judge.md
- Frontmatter: name: judge; description: "Strict Opus reviewer of a finished diff against acceptance criteria. Only at /delegate checkpoints or when the user asks for a review."; model: opus; effort: high; tools: Read, Grep, Glob, Bash; disallowedTools: Edit, Write, NotebookEdit; maxTurns: 20; color: red.
- Inputs: the criteria plus `git diff`.
- Checks: correctness, criteria coverage, tests that test nothing, security, risky edge cases. No style nits.
- RETURN: `VERDICT: PASS|FAIL` + ≤10 numbered lines `path:line — defect — fix hint (sev)`, or PASS + the single biggest residual risk. No praise, no restating the diff.

All Haiku work stays well under 100k context (5x price tier above it), via maxTurns and the stop rules.

Plugin agents ignore permissionMode, hooks and mcpServers, so none are used.

## COMPONENT: Explore override on Haiku (templates/Explore.md → ~/.claude/agents/Explore.md)
- **kind**: subagent
- **invocation**: auto (Claude's habitual Explore use). Installed by /hetz:setup global as an opt-in, recommended once verified.
- **purpose**: Move Claude's most frequent delegation, codebase exploration, to Haiku 5.5 (40x cheaper input than Opus 5.5) without adding a competing description to context. It also serves as the interview's and delegate's 'scout'.
- **derivedFrom**: delegation (match models to tasks); max-savings' explore-on-haiku; replaces workflow-ux's scout and minimal's grunt search role.
- **estimatedImpact**: HIGH where Explore is used, which is often. The dossier is inconsistent on the built-in's model (Opus alias on subscription/Console vs inherit-main), but on a measured developer's Opus/Sonnet sessions either way it moves 10–50k-token read-heavy runs to Haiku. Cold start about 24.5k tokens (≈$0.003).
- **alwaysOnContextCost**: About 0 net: it replaces the built-in Explore description with one of similar length.
- **risks**: - Haiku misses subtle logic and the main thread re-explores; the bench measures the re-search rate, with `model: sonnet` as the fallback.
- It pins behavior, so future built-in Explore prompt improvements are lost; check monthly.

### contentOutline
Frontmatter:
- name: Explore
- description: Read-only codebase search: find files, symbols, call sites, how X works. Returns path:line facts.
- model: haiku
- effort: low
- tools: Read, Grep, Glob, Bash
- disallowedTools: Edit, Write, NotebookEdit
- maxTurns: 25
- omitClaudeMd: true (v2.1.271+, matching the built-in)

Body:
- Grep/Glob first; Read with offset/limit.
- Never read lockfiles, minified, map or generated files.
- Bash read-only (git log/show/grep, ls).
- Stop as soon as the question is answered.
- If the answer needs more than ~25 files, return a partial map plus what remains (keeps Haiku under 100k).
- RETURN:
  - `Answer:` 1–2 lines
  - `Evidence:` ≤15 `path:line — fact`
  - `Not found / not checked:`
  - no code blocks over 5 lines

Verify: `/agents` lists Explore (user) on haiku.

Fallback if a user-scope same-name agent does not override the built-in on the installed version: ship plugins/hetz-crew/agents/scout.md with the same body, description 'Haiku read-only code search; use for where/how questions needing >3 file reads' (+~20 tokens). Point interview and delegate at hetz-crew:scout.

Removing the file restores the built-in.

## COMPONENT: /hetz:handoff
- **kind**: skill
- **invocation**: user: /hetz:handoff [focus]. disable-model-invocation: true. Pickup is automatic via session-guard (b).
- **purpose**: Make /clear cheap and safe mid-project by carrying a dense state note of ≤40 lines forward, instead of a compounding thread or a full-context /compact.
- **derivedFrom**: rotate sessions; all three designs (mattpocock handoff, ykdojo HANDOFF.md)
- **estimatedImpact**: HIGH as an enabler: about 600 tokens carried instead of 100–200k, on every later request. Late requests in long threads dominated the user's largest session.
- **alwaysOnContextCost**: 0 standing. About 250 tokens on invoke, discarded by the /clear that follows.
- **risks**: - A lossy note drops a detail: mitigated by path:line refs, dead ends and verify commands.
- A stale note: the pickup is limited to 12 h and injected once.

### contentOutline
Frontmatter:
- name: handoff
- description: Save session state to a ≤40-line note so you can /clear and continue cheaply.
- disable-model-invocation: true
- argument-hint: [focus]
- allowed-tools: Write(.claude/handoff.md), Bash(git status*), Bash(git diff --stat*), Bash(git log --oneline*)

Body (~250 tokens):
1. Run `git status -s`, `git diff --stat` and `git log --oneline -n 5`.
2. Overwrite .claude/handoff.md (≤40 lines), focused on $ARGUMENTS if given:
   - Goal (1 line)
   - Done (with commit SHAs)
   - In progress (exact state, uncommitted files)
   - Decisions + one-line why
   - Key locations path:line (≤10)
   - Verify commands (exact, with quiet flags)
   - Next steps (≤3, ordered)
   - Dead ends: tried and failed, don't retry
   - Spec path if any
3. Reference paths. Never paste code over 5 lines, logs, env values or secrets.
4. Ensure `.claude/handoff.md` is in .git/info/exclude; never edit .gitignore. Outside git, write to ~/.claude/hetz/handoff/<cwd-hash>.md.
5. Recommend exactly one next action:
   - unrelated next task → `/rename <slug>`, then `/clear` (free; the note is auto-offered in the new session)
   - same task with ctx <150k and a warm cache → `/compact <generated one-line focus>` is fine instead
   - abandoning an approach → `/rewind`
   - side question → `/btw`
   Skills cannot run /clear or /compact, so print the exact command to type.

## COMPONENT: /hetz:slim (always-loaded context + deny audit)
- **kind**: skill
- **invocation**: user: /hetz:slim [--deny-only] (once per repo and after big CLAUDE.md edits; /hetz:setup repo calls its deny part). disable-model-invocation: true
- **purpose**: The user's 'readme' and 'ignore' ideas, corrected and merged. Shrink everything billed on every request and in every subagent, and replace .claudeignore with Read deny rules.
- **derivedFrom**: readme + ignore (merged): minimal's audit, max's slim + noise-deny, workflow-ux's slim classification. caveman-compress's goal kept, its method rejected.
- **estimatedImpact**: LOW–MEDIUM in dollars: a 5k → 1.5k-token CLAUDE.md saves about 1.75M cache-read tokens per 500 requests, times each subagent that loads it. MEDIUM through adherence. Each prevented bundle or map read saves 5–25k tokens for the rest of the session.
- **alwaysOnContextCost**: 0 standing. About 350 tokens plus a ≤40-line report on invoke; run it in its own session, then /clear.
- **risks**: - Removing a rule the team relies on: diff and confirm only.
- Over-denying blocks Edit and pushes Claude to `python open()`/`grep -r` workarounds that cost more; presets are conservative.
- Path-scoped rules drop out after compaction until re-triggered.
- A team-owned CLAUDE.md should get a PR, not a silent edit.

### contentOutline
Frontmatter:
- name: slim
- description: Audit this repo's always-loaded context and noisy files; propose trims as one diff.
- disable-model-invocation: true
- allowed-tools: Bash(python3 *slim_audit.py*), Read, Edit, Write(.claude/rules/**), Write(.claude/skills/**), AskUserQuestion

The body starts with !`python3 -I "${CLAUDE_SKILL_DIR}/slim_audit.py"`. The script is deterministic and prints ≤40 lines.

slim_audit.py reports:
- Each CLAUDE.md and CLAUDE.local.md from cwd up to /, plus ~/.claude/CLAUDE.md: lines and est. tokens (chars/4); yellow >60 lines, red >200; @imports resolved and sized.
- .claude/rules/*.md without `paths:` (always loaded).
- Auto memory: the first 200 lines / 25 KB of ~/.claude/projects/<proj>/memory/MEMORY.md.
- .claudeignore present → 'NO EFFECT — migrate to Read deny'.
- Read deny rules in effect (user, project, local merged).
- Deny candidates, excluding anything gitignored or already denied, each with size and est. tokens:
  - generated dirs: dist build out .next .nuxt coverage target vendor third_party generated
  - *.min.*, *.map, *.snap
  - data or fixtures >200 KB
  - logs
  - lockfiles (unchecked by default)
  - never source, config, or files Claude edits
- MCP servers configured (hint: prefer CLIs like gh; disable with /mcp).
- CLAUDE_CODE_GLOB_NO_IGNORE status.
- With --plugins: `claude plugin details` always-on tokens per enabled plugin; flags overlaps (caveman + Concise, RTK + squeeze).

Body instructions (~350 tokens):
1. Summarize in ≤10 lines, biggest standing cost first.
2. Classify each CLAUDE.md section:
   - KEEP: non-inferable commands, hard always/never rules, non-obvious conventions, output-length rules
   - SCOPE → .claude/rules/<area>.md with `paths:` globs
   - SKILL → .claude/skills/<name>/SKILL.md with disable-model-invocation (release/deploy/migration procedures)
   - COMMENT → `<!-- -->` block (stripped, free)
   - DOCS → docs/ referenced by path (not @import, which saves nothing)
   - DELETE: derivable from code, generic advice, duplicates of Concise
   Targets: root ≤60 lines (~800 tokens), never above the 200-line ceiling. Rewrite kept items as imperative one-liners.
3. Add `## Compact instructions` (3 lines):
   - KEEP: goal, decisions + why, touched paths, failing tests with decisive lines, verify commands
   - DROP: exploration output, resolved errors, file contents, tool logs
4. MEMORY.md: dedupe and drop stale entries, target ≤50 lines.
5. Deny rules: per-group choice, written as `Read(./dist/**)` to .claude/settings.local.json (default) or .claude/settings.json (team). Offer migrating .claudeignore entries, then deleting the file.
6. Show one unified diff and ask once (all / pick / none) with AskUserQuestion. Apply. Git is the backup; untracked files get .hetz-bak.
7. End: 'CLAUDE.md changes apply after /clear; deny rules in new sessions. Self-test: ask Claude to Read one denied file.'

Never run an automatic LLM compression rewrite.

## COMPONENT: docs/ playbook (PLAYBOOK.md, EVIDENCE.md, DECISIONS.md, quiet-flags.md, rtk-optional.md)
- **kind**: doc
- **invocation**: human-only; never @imported or auto-loaded
- **purpose**: Hold 'all the knowledge of how to save tokens' at zero runtime cost: the habits no hook can enforce, the cost model, and the evidence behind every choice.
- **derivedFrom**: all ideas (human side); all three designs
- **estimatedImpact**: Indirect but real. Rotation, model choice and cache discipline are human decisions that are a large share of the levers.
- **alwaysOnContextCost**: 0. The hetz repo's own CLAUDE.md (≤20 lines) must not import it.
- **risks**: Goes stale fast. Date and version-tag every claim; check.sh warns when `claude --version` differs from the version recorded in EVIDENCE.md.

### contentOutline
PLAYBOOK.md (one page):
1. Cost model: every request re-sends context; cache read is 0.05x on Opus/Sonnet 5.5; a bust is about 25–40x a warm turn at 150k; output is about 7% of a measured developer's spend; price table.
2. Session habits:
   - /rename + /clear between tasks (free)
   - /compact <focus> only while warm
   - /rewind instead of a second correction; after 2 failed corrections, /clear and re-prompt
   - /btw for side questions; /recap keeps the cache
   - don't resume big sessions after the TTL — handoff and start fresh
3. Cache hygiene:
   - pick model and effort at start (cc/cc-deep/cc-quick)
   - never /model mid-session; avoid opusplan
   - a skill with `model:` is a switch
   - add MCP and plugins before starting
   - crop screenshots (1080p ≈ 2.7k tokens) or paste error text
4. Model/effort routing table and when to delegate: 'will I need the output, or only the conclusion?' Effort is already medium on 5.5; thinking can't be turned off there. /advisor is API-only.
5. Background sinks: /loop, scheduled tasks, goal check-ins; agent teams ≈7x; worktrees and parallel sessions multiply tokens.
6. Prompts: name files and constraints; plan mode for multi-file work; prefer CLIs over MCP; LSP plugins for typed languages; scripted runs with `claude -p --bare --max-budget-usd`.

Other files:
- quiet-flags.md: the per-tool cheat sheet; source of share/quiet-flags.tsv.
- rtk-optional.md: rtk-oss via `brew install rtk-ai/tap/rtk-oss`, `rtk init -g --hook-only`, exclude_commands for git diff/cat/ls; A/B it first; never with squeeze on the same commands.
- EVIDENCE.md: every claim with source, confidence, Claude Code version and date, plus the ledger baseline.
- DECISIONS.md: mirrors rejectedOrChanged.
- myths: .claudeignore does nothing; CLAUDE.md isn't re-read from disk; delegation isn't automatically cheaper; caveman is about 3% beyond 'be concise'; DISABLE_NON_ESSENTIAL_MODEL_CALLS doesn't exist.

## COMPONENT: measurement harness (scripts/hetz_ledger.py, scripts/check.sh, bench/)
- **kind**: script
- **invocation**: user / CI (developer of hetz). Never installed into sessions; ledger also runnable from the terminal at 0 tokens.
- **purpose**: Prove 'if they are good' with billed-token evidence, and keep the kit under its own token budget.
- **derivedFrom**: NEW, implied by the user's 'if they are good'. Union of minimal's usage-report and replay, max's hz-ledger and bench, workflow-ux's audit and decision rule.
- **estimatedImpact**: Enabling. Without it the kit just repeats vendor numbers. It also decides each component's default on/off state.
- **alwaysOnContextCost**: 0
- **risks**: - A full matrix costs about $120–400; run the cheap pair (A vs D on tasks 3, 4, 7) per change and the full matrix monthly or after a Claude Code upgrade.
- High variance (JetBrains' -29.5% did not replicate): pin model, effort and version, k≥5.
- Transcripts under-record output.
- List price is not the subscription bill, so compare relative Δ only.

### contentOutline
scripts/hetz_ledger.py (stdlib; reads only usage numbers, tool names, result lengths and timestamps from ~/.claude/projects/**/*.jsonl, including subagents/; dedupes by message id):
- spend split by token type at list prices (hooks/prices.json)
- cost by context bucket: <50k, 50–150k, 150–300k, 300–600k, >600k
- cache-break events (cache_creation >20k after the first request) with the likely cause: model change, gap > TTL, compaction, resume
- subagent cost by type and model, including cold-start overhead
- top Bash command families by result chars
- squeeze realized savings from squeeze.tsv (chars removed × later requests × cache-read price)
- Explore, runner and judge adoption
- $/active day, $/session, sessions over $5

Flags: --since 14d, --project, --session, --json, `--compare A..B C..D`, and `--replay-squeeze`, which runs historical Bash results through squeeze.py offline and reports chars saved by command plus KEEP lines lost (must be 0).

scripts/check.sh (pre-commit + GitHub Actions):
- `claude plugin validate . --strict` and per plugin
- `claude --plugin-dir plugins/hetz plugin details hetz` → assert 0 always-on tokens
- hetz-crew → assert ≤80
- grep that every SKILL.md has disable-model-invocation: true
- pytest hooks/tests (squeeze shape, git diff untouched, every hook fails open on garbage stdin, guard never prints plain stdout)
- `claude plugin eval` suites with free regex and tool_used graders:
  - 'run the full test suite' → runner
  - 'where is X handled' → Explore
  - a one-line question → no subagent
  - judge never invoked unprompted

bench/:
- tasks/<name>/{task.md, fixture.json (git url+sha), check.sh, max_turns}. Eight tasks:
  1. failing Python test
  2. TS feature with hidden tests
  3. large-repo Q&A graded on key facts
  4. full suite with failures
  5. noisy broken build
  6. multi-file rename
  7. three unrelated tasks in one session
  8. ambiguous request run through /hetz:interview with scripted answers
- arms/*.json: --settings file + --plugin-dir list:
  - A: baseline with Concise (the control is 'be concise', not nothing)
  - B: settings profile only
  - C: B + hetz hooks
  - D: C + crew + Explore override
  - D + sonnet-main
  - leave-one-out ablations: -cap, -squeeze, -contract, -crew, -explore, -quietenv
- run.py: `claude -p "$(cat task.md)" --output-format json --setting-sources project,local --settings <arm> --plugin-dir … --model <pinned> --max-turns N --max-budget-usd 3`, k=5 per cell, randomized, one warm-up discarded.
- report.py: median cost, IQR, pass rate, Δ% with bootstrap 95% CI.

# REJECTED/CHANGED
- **caveman (install the plugin / ship a caveman skill)** → Changed: built-in Concise output style plus terse return contracts (the SubagentStart hook and the crew/Explore RETURN formats). A custom terse style only as a bench arm, never force-for-plugin.: Output is about 7% of a measured developer's list-price spend. Caveman's own eval shows -3% output beyond 'Answer concisely'; JetBrains measured -8.5% with flat quality. That is about 1% of the bill, against about 1.2k input tokens plus an ~85-token per-turn reminder, Node hooks and default telemetry. Brevity compounds only on the input side (subagent results re-read by the parent). Pending trademark: never name anything 'caveman'. max-savings' force-for-plugin style was dropped because it overrides the user's own outputStyle in every repo.
- **RTK** → Changed: not installed by default. Replaced by bashOutputMaxChars + squeeze (PostToolUse, successful output only, never rewrites commands) + Bash-only quiet env + the runner agent. RTK documented as opt-in (rtk-oss, --hook-only), never on the same commands as squeeze.: RTK is a binary plus a PreToolUse rewriter, not a skill, and covers Bash only. It has open bugs where failures show green or exit codes are masked (#4421, #2974, #3267). A disputed eval found it 16% more expensive per passing run. The default build carries a BUSL engine, and 1.0 is 4 days old. Its 60–90% is shell bytes, not the bill. The large Bash outputs in the user's history were file dumps, which RTK's cat-summarizing would make lossy.
- **interview: keep probing until everything is figured out** → Kept and strengthened as the front of a pipeline (/hetz:interview → spec → /clear → /hetz-crew:delegate). Probing continues while high-rework decisions are open, with a checkpoint after round 3 rather than a hard cap. User-invoked only.: Questions at near-zero context are the cheapest tokens and prevent the most expensive ones (wrong work). Unbounded grilling on trivia wastes rounds and the user's time, so facts are looked up via Explore, low-impact nodes get stated defaults, and 'enough' ends it. A model-invocable version would bill its description in every repo. minimal-footprint's hard 3-round cap was relaxed to honor the user's intent.
- **delegation: spin up models per task complexity; strong model judges** → Kept, made concrete and routed: Haiku Explore override + Haiku runner + Sonnet worker + Opus judge only at checkpoints, orchestrated by /hetz-crew:delegate. Main model is chosen at session start via launchers or setting; never /model mid-session.: Subagents are not cheaper by default: cold starts are about 24.5k tokens (Haiku 5.5) to about 46k (Opus 5.5), with a 5-minute cache, and multi-agent setups use about 7–15x the tokens. They pay off when bulky output stays out of an expensive parent and cheaper models do the legwork. A judge after every step re-reads everything, so it runs once per change. minimal-footprint's removal of the judge was reverted because the judge is what makes a Sonnet main session safe and is core to the user's idea.
- **ignore: add every unnecessary thing to .claudeignore** → Rejected as specified (.claudeignore has no effect; absent from the 2.1.272 binary). Replaced by CLAUDE_CODE_GLOB_NO_IGNORE=false, a tiny anchored global deny (min/map), and per-repo candidates from /hetz:slim written to settings.local.json by default.: Grep already respects .gitignore; Glob does not by default, which is the real gap. A Read deny also blocks Edit and Write, and doesn't stop subprocesses, so 'every unnecessary thing' (lockfiles, node_modules, snapshots) would break legitimate work. minimal-footprint's global lockfile deny was dropped for that reason, and its bare-name user-scope patterns were replaced with `//`-anchored ones.
- **readme: long instruction files get re-read every turn** → Premise corrected; goal kept in /hetz:slim (root ≤60 lines, path-scoped rules, HTML comments, skills for procedures, Compact instructions, MEMORY.md diet).: CLAUDE.md is loaded once at session start, not re-read from disk. It is re-sent on every request (more often than every turn) at the cache-read rate and loaded into most subagents. Edits apply only after /clear or /compact. @imports save nothing. The dollar effect is modest; adherence is the stronger reason to trim.
- **rotate sessions as a skill that clears or compacts** → Changed: skills cannot run /clear or /compact. Mechanized via autoCompactWindow=200000, tuned native idle nudge, session-guard systemMessages (size thresholds, commit boundary, cold resume, model switch), statusline, and /hetz:handoff + automatic one-time pickup.: The largest measured waste is context growth: in the user's biggest session, 63% of cost came from requests over 600k context and 90% from requests over 300k. A settings cap bounds the worst case, and free /clear plus a handoff note beats any instruction text. workflow-ux omitted the cap, which is its largest gap.
- **Quiet flags as always-on model instructions / house-rules block in ~/.claude/CLAUDE.md** → Rejected as default (kept only as a bench arm). Quiet behaviour comes from CLAUDE_ENV_FILE env (deterministic) plus squeeze's just-in-time footer tip.: Always-on text is billed on every request in every repo and can't be toggled per plugin. Env vars apply without the model remembering anything, and the tip teaches at the moment of waste. A house block graduates only if its ablation pays for its roughly 100 tokens.
- **Quiet-tool vars (NO_COLOR, npm_config_*, PYTEST_ADDOPTS) in settings env (minimal-footprint)** → Changed: written to CLAUDE_ENV_FILE from a SessionStart hook, so they reach Claude's Bash only.: Settings env also applies to the Claude Code process itself (NO_COLOR can strip its own UI colors). Plugin-delivered env is toggled with the plugin and needs no settings write. PYTEST_ADDOPTS and CI=1 change tool behaviour, so they are in the aggressive opt-in only.
- **output-coach PostToolUse additionalContext (workflow-ux)** → Merged into squeeze's footer tip: Additional context adds model tokens without removing the waste that already entered context. A tip appended to output that was just shrunk is net-negative tokens.
- **PreToolUse command rewriter (the costs-doc example piping test output to grep or head)** → Rejected: The pipe masks the exit code (head decides it), a passing run returns empty output, and 'allow' skips permission prompts. Safe matching means re-implementing permission parsing (RTK's complexity). PostToolUse replacement leaves the executed command untouched.
- **PreModelSwitch 'ask' permission decision (max-savings)** → Changed to a non-blocking systemMessage: The decision shape for PreModelSwitch is unverified on 2.1.272, and friction on every deliberate switch is poor UX across many repos. Showing the dollar cost at the moment of switching captures most of the value.
- **hz-run wrapper on PATH, lean output style, separate review skill, cost skill, deny-noise skill (max-savings)** → Merged or dropped to keep the component count tight: runner absorbs hz-run's log-to-file pattern. Concise covers lean. The crew judge covers review (inside delegate, or invoked directly by asking for a review). The ledger is a terminal script at 0 tokens. deny-noise is part of /hetz:slim and /hetz:setup repo.
- **hetz-lab plugin with experimental terse style and trim hook (workflow-ux)** → Changed: experiments live as bench arms, not an installable plugin: Fewer install surfaces; squeeze already contains the trim logic behind a gate.
- **Model or effort keys on main-thread skills (e.g. /delegate on Opus); opusplan** → Rejected: A skill naming a different model is a model switch for that turn, and each opusplan toggle is one. Both rebuild the whole cached prefix (about 25–40x a warm turn at 150k). Models are pinned only on subagents, or chosen at session start.
- **One skill per idea, model-invocable** → Rejected: 5 user-invoked skills, 0 always-on listing tokens in hetz; the only listing cost is about 70 tokens of crew descriptions in a separately toggleable plugin: Every model-invocable description is billed on every request in every repo, and skillOverrides cannot silence plugin skills; the plugin boundary is the only on/off switch.
- **Global CI=1, MAX_THINKING_TOKENS=0, DISABLE_NON_ESSENTIAL_MODEL_CALLS, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC** → Rejected (CI=1 only in HETZ_QUIET_ENV=aggressive): CI=1 changes behaviour (jest snapshots, warnings treated as errors). Thinking can't be disabled on the 5.5 models (use effort instead). The third doesn't exist in 2.1.272. The fourth also kills updates, telemetry and the advisor.
- **Proxies and context packers (context-mode, headroom, pxpipe, claude-code-router, caveman Auto, repomix, claude-mem)** → Rejected as defaults; listed in the playbook: They are invasive or restrictively licensed (context-mode is ELv2), carry fidelity risk, and add context rather than remove it. Any ANTHROPIC_BASE_URL proxy loads all MCP definitions upfront unless ENABLE_TOOL_SEARCH=true. All savings figures are self-reported.
- **Worktrees, parallel sessions, agent teams as savers; batch-edit, grep-before-read and re-read-dedupe skills** → Rejected: Parallelism multiplies tokens (teams about 7x). Ranged reads, read dedupe, Grep head_limit and parallel tool calls are already native in 2.1.272.

# PACKAGING
The repo at ~/Projects/hetz is one marketplace with two plugins. This is pattern B, which passed `claude plugin validate` on 2.1.272.

```text
hetz/
├── .claude-plugin/marketplace.json     # name "hetz", owner {name: "Sahar Gavriely"}; plugins: hetz -> ./plugins/hetz, hetz-crew -> ./plugins/hetz-crew
├── plugins/
│   ├── hetz/                            # 0 always-on listing tokens
│   │   ├── .claude-plugin/plugin.json   # {name:"hetz", description:"Token savers: output squeeze, session guard, interview, handoff, slim, setup", author}; NO "version"
│   │   ├── hooks/hooks.json             # {"hooks":{SessionStart(startup|resume|clear|compact)->guard.py, UserPromptSubmit->guard.py, PreModelSwitch->guard.py, PostToolUse(Bash)->squeeze.py, SubagentStart(Explore|general-purpose)->contract.py}}
│   │   ├── hooks/{guard.py, squeeze.py, contract.py, prices.json}, hooks/tests/
│   │   ├── share/quiet-flags.tsv
│   │   ├── scripts/{hetz_settings.py, statusline.py}
│   │   ├── profiles/{safe.json, optional.json}, templates/{Explore.md, compact-instructions.md, launchers/*.fish|zsh|bash}
│   │   └── skills/{setup, interview, handoff, slim/(SKILL.md, slim_audit.py)}/SKILL.md   # all disable-model-invocation: true
│   └── hetz-crew/                       # only listing cost (~70 tokens), toggle separately
│       ├── .claude-plugin/plugin.json
│       ├── agents/{runner, worker, judge}.md   (+ scout.md only if the Explore override proves unsupported)
│       └── skills/delegate/(SKILL.md, references/routing.md)
├── scripts/{install.sh, uninstall.sh, hetz_ledger.py, check.sh}
├── bench/{run.py, report.py, arms/*.json, tasks/*}
├── docs/{PLAYBOOK.md, EVIDENCE.md, DECISIONS.md, quiet-flags.md, rtk-optional.md}
├── CLAUDE.md                            # ≤20 lines: layout, "run scripts/check.sh before commit", budget rule (hetz = 0, hetz-crew ≤ 80 always-on tokens), never @import docs/
└── README.md                            # install, uninstall, per-component cost, measured results table
```

**Runtime**
- Python 3 stdlib only. No Node, no jq, no third-party binaries.
- install.sh records the real interpreter path in ~/.claude/hetz/py, so hooks skip the pyenv shim.
- Every hook fails open (exit 0, no stdout) on any error.

**Install for every repo** (user scope is the default, so it applies to all projects):
1. Run `~/Projects/hetz/scripts/install.sh`. It runs `claude plugin marketplace add ~/Projects/hetz`, then `claude plugin install hetz@hetz` and `claude plugin install hetz-crew@hetz`.
   - The marketplace is a directory source, so it loads in place: edits apply after `/reload-plugins` or in the next session, with no version bump.
2. In any session, run `/hetz:setup global` once per machine. It applies the settings profile (preview, backup, manifest), copies the statusline to ~/.claude/hetz/, and optionally writes ~/.claude/agents/Explore.md and shell launchers.
3. Optionally run `/hetz:setup repo` or `/hetz:slim` once per repo. Per-repo deny rules go to .claude/settings.local.json by default.

**Uninstall**
- `/hetz:setup undo global`, or `scripts/uninstall.sh`, which works without Claude.
- Then `claude plugin uninstall hetz-crew@hetz hetz@hetz` and `claude plugin marketplace remove hetz`.

**Partial off switches**
- `claude plugin disable hetz-crew@hetz`.
- Env flags: HETZ_SQUEEZE=0, HETZ_GUARD=0, HETZ_CONTRACT=0, HETZ_QUIET_ENV=off.
- Per repo: `"enabledPlugins": {"hetz-crew@hetz": false}` in that repo's .claude/settings.json.

**Dev loop**
- `claude --plugin-dir ./plugins/hetz --plugin-dir ./plugins/hetz-crew`, then `/reload-plugins` after edits.
- Debug with `/hooks` and `claude --debug-file f.txt`.
- Run `scripts/check.sh` before each commit.

**Other machines**
- Push to GitHub, then `claude plugin marketplace add sahargavriely/hetz`. It is versioned by commit SHA because there is no `version` field.
- This is why the statusline is copied out of ${CLAUDE_PLUGIN_ROOT}.

**Version and verification**
- Target Claude Code 2.1.272 or later. Newer-only features are not used (onFailure, agent autoCompactWindow, /doctor prompt-audit, idleCompaction).
- Verify before first release:
  - PreModelSwitch and the SessionStart cost fields
  - the exact updatedToolOutput Bash shape
  - PostToolUse vs PostToolUseFailure on non-zero exit
  - CLAUDE_ENV_FILE on SessionStart
  - top-level autoCompactWindow
  - `//`-anchored user deny rules
  - the user-scope Explore override
  - an initial-prompt slash command in the launchers
- Each item that fails verification has a stated fallback in its component.

**Naming**
- `hetz` and `hetz-crew` avoid the reserved claude-/anthropic- prefixes and the caveman trademark.
- Commands are /hetz:setup, /hetz:interview, /hetz:handoff, /hetz:slim and /hetz-crew:delegate.

# MEASUREMENT
Measurement runs in four layers, from cheapest to most expensive. A component is on by default only if it passes layers 1 and 2. Layer 3 confirms the result on real work. Anything that fails is moved to opt-in or deleted.

**Layer 1: static budget (free, every commit, scripts/check.sh)**
- `claude plugin validate --strict` passes for the marketplace and for each plugin.
- `claude plugin details` reports 0 always-on tokens for hetz and 80 or fewer for hetz-crew.
- Every SKILL.md has `disable-model-invocation: true`.
- Hook pytest suites check four things:
  - squeeze's output matches the expected shape.
  - git diff, cat and grep output is never altered.
  - No KEEP line is ever dropped.
  - guard.py never prints plain stdout on SessionStart or UserPromptSubmit, and every hook exits 0 on garbage input.
- `claude plugin eval` trigger cases, using free regex and tool_used graders:
  - A full test run goes to runner.
  - 'Where is X' goes to Explore.
  - A one-line question uses no subagent.
  - The judge is never invoked unprompted.
- In a fresh session, /context confirms the Concise and agent rows and the 200k compaction window.

**Layer 2a: offline replay (free)**
- Command: `hetz_ledger.py --replay-squeeze`.
- It feeds every historical Bash tool_result from ~/.claude/projects through squeeze.py.
- Gate: squeeze becomes default-on only if at least 2 weeks of real dev sessions show 15% or more of Bash result characters removed, with 0 KEEP lines lost. Until then the default is HETZ_SQUEEZE=0.
- Known baseline:
  - Bash accounts for 52% of tool-result characters (1.69M).
  - 223 of 699 calls returned more than 2k characters, mostly file dumps that squeeze deliberately leaves alone.

**Layer 2b: controlled A/B bench (bench/; costs real tokens, so every run uses --max-budget-usd)**
- Tasks: the 8 tasks with check.sh pass/fail verifiers, on pinned fixture repos.
- Isolation: each run uses `--setting-sources project,local`, so the user's personal plugins and settings don't leak in.
- Arms:
  - A: baseline plus Concise. The control is "be concise", not nothing, because caveman's numbers show most of the gap from "nothing" comes from that one sentence.
  - B: settings profile.
  - C: B plus hetz hooks.
  - D: C plus hetz-crew and the Explore override.
  - D with a Sonnet main model.
  - Leave-one-out ablations of D: -cap, -squeeze, -contract, -quietenv, -crew, -explore.
- Repetitions: k=5 per cell, in randomized order, with one warm-up run discarded. Model, effort and Claude Code version are pinned.
- Metrics:
  - total_cost_usd.
  - input, cache_read, cache_write and output tokens, cross-checked against hetz_ledger --json over the main and subagent transcripts.
  - Pass rate.
  - Peak and median context.
  - Cache-break count.
  - Turns.
- Decision rules:
  - The suite (D vs A) must cut median cost by 10% or more with a non-inferior pass rate (at most 1 extra failure across k runs).
  - Each component's ablation must show 3% or more cost reduction on its target tasks, or 20% or more lower peak context, with a bootstrap 95% CI that excludes 0 and no drop in quality.
- Interview and delegate have no clean token metric, so they are judged differently:
  - On tasks 2 and 8, compare total tokens to the first passing verify, the number of correction turns, and the pass rate, with and without /hetz:interview, and with /hetz-crew:delegate versus plain implementation.
  - Handoff: run task 7 as a single thread versus handoff plus /clear between subtasks.
- Cadence: the cheap pair (A vs D on tasks 3, 4 and 7) runs on every change. The full matrix runs monthly or after any Claude Code upgrade.

**Layer 3: field ledger on the user's real work (0 tokens; terminal `scripts/hetz_ledger.py`)**
- Snapshot now: `--since 30d --json > .claude/ledger/baseline.json` (git-ignored: never commit personal usage).
- Re-run after 14 days with `--compare`. Normalize per active day, per session and per request.
- Track:
  - Cost share above 300k context. Target: about 0, from the 200k cap.
  - Cache-write share and cache breaks by cause. Target: falling, from session-guard.
  - Median and p95 context per request.
  - Bash characters per call, and squeeze's realized savings.
  - Subagent cost share by model, and Explore/runner/judge adoption.
  - Sessions over $5.
- Cross-check against /usage (cache hit %, attribution by plugin, skill and subagent) and the official session-report plugin.
- Treat this layer as confirmation, not proof, because the work being done differs between the two windows.

**Layer 4: live feedback**
- The statusline shows context, cache warm/cold and $.
- session-guard's systemMessages make each avoided cost visible at the moment of decision.

**Reporting**
- Record every result in docs/EVIDENCE.md with k, model, effort and Claude Code version, so the kit's claims are its own measurements.
- Caveats:
  - total_cost_usd is a list-price estimate, not the subscription bill, so compare relative changes only.
  - Transcripts under-record output tokens.
  - Byte-level savings (chars in and out) are only proxies for billed tokens.
