# hetz decisions

What happened to each of your original ideas, and to every proposal that was rejected or changed during design. This file mirrors "REJECTED/CHANGED" in [DESIGN.md](DESIGN.md), rewritten for humans. Bracketed IDs point to rows in [EVIDENCE.md](EVIDENCE.md). Claude Code 2.1.272, 2026-10-17.

Every entry ends with **Revisit if**: the measurement that would change the decision.

## Summary

| Your idea | Verdict | Where it lives now |
|---|---|---|
| caveman | Changed | Built-in Concise style plus terse return formats for subagents |
| RTK | Changed (opt-in only) | `bashOutputMaxChars` + squeeze hook + quiet env + runner agent; RTK is documented in [rtk-optional.md](rtk-optional.md) |
| interview | Kept and strengthened | `/hetz:interview` → spec → `/clear` → `/hetz-crew:delegate` |
| delegation | Kept, made concrete | Explore on Haiku, runner, worker, judge, `/hetz-crew:delegate` |
| ignore (.claudeignore) | Rejected as specified, replaced | Read deny rules, the Glob fix, `/hetz:slim` |
| readme (long instruction files) | Premise corrected, goal kept | `/hetz:slim` |
| rotate sessions | Changed: mechanised around you | 200k compact cap, session-guard nudges, statusline, `/hetz:handoff` |
| verbose terminal output | Kept, split across layers | output cap, quiet env, squeeze, [quiet-flags.md](quiet-flags.md), runner |

## Your ideas

### 1. caveman: make Claude answer in terse "caveman" style
**Verdict: changed.** hetz does not install caveman or ship a caveman-style skill.

**Instead:**
- `/hetz:setup` enables the first-party `Concise` output style, but only if you have no style set.
- Brevity goes where it compounds: a SubagentStart hook gives Explore and general-purpose agents a short return format, and the crew agents and the Explore override have strict RETURN formats.
- A custom terse style exists only as a bench arm.

**Why:**
- Output is ~7% of your list-price spend [COST-7].
- caveman's own eval shows −3% output beyond plain "Answer concisely." [STY-2]. JetBrains measured −8.5% output with flat quality [STY-4]. Together that is under 1% of the bill [STY-5].
- The cost side: ~1.2k rule tokens per session, a ~85-token reminder every turn, Node hooks and default telemetry [STY-3].
- Brevity pays more on the input side. A subagent's result is re-read on every later parent request, and output styles don't reach subagents [SUB-8]. That is why the return contracts exist.
- Naming: nothing in hetz is called "caveman" (pending trademark).
- max-savings' forced plugin output style was dropped, because it would override your own style in every repo.

**Revisit if:** a bench arm with a custom terse style beats Concise on cost at an equal pass rate by more than its standing tokens.

### 2. RTK: compress command output with Rust Token Killer
**Verdict: changed (not installed by default).**

**Instead:**
- `bashOutputMaxChars` 12000 [OUT-1].
- The squeeze hook: PostToolUse, successful output of allowlisted noisy tools only, never rewrites commands, saves the raw log.
- Quiet env vars, reaching Bash only.
- The Haiku `runner` for verbose runs.
- RTK stays an opt-in, documented in [rtk-optional.md](rtk-optional.md): `rtk-oss`, hook-only, never on the same commands as squeeze.

**Why:**
- RTK is a binary plus a PreToolUse command rewriter, not a skill, and it covers Bash only [RTK-1].
- Its 60–90% is shell-output bytes, not your bill [RTK-2].
- Open bugs can show failures as green or mask exit codes [RTK-3], and one eval found it 16% more expensive per passing run (disputed) [RTK-4].
- The default build links a BUSL engine, and 1.0 was 4 days old on 2026-10-17 [RTK-5, RTK-6].
- The largest measured Bash outputs were file dumps [COST-9]. RTK summarises `cat`, which would make those lossy.

**Revisit if:** an A/B per rtk-optional.md shows ≥3% lower median cost at an equal pass rate on your own tasks.

### 3. interview: keep probing until everything is figured out
**Verdict: kept and strengthened.** It is the front of a pipeline: `/hetz:interview` → `.claude/specs/<slug>.md` → `/clear` → `/hetz-crew:delegate`. It is user-invoked only, so it has zero listing cost.

**How "keep probing" is honoured:**
- It keeps asking while any high-rework decision is open, with a checkpoint after round 3 ("continue or accept my defaults?") instead of a hard cap.
- It looks up facts with Explore instead of asking you.
- Low-impact choices get stated defaults.
- Typing "enough" ends it at any time.

**Why:**
- Questions asked at near-zero context are the cheapest tokens, and they prevent the most expensive ones: wrong work.
- The fresh session starts at spec size (~1k tokens) instead of carrying the whole Q&A.
- The official best practices recommend the same interview → spec → fresh-session flow [MISC-7].
- minimal-footprint's hard 3-round cap was relaxed to honour your intent. Unbounded grilling on trivia was rejected because it wastes rounds and your time.

**Revisit if:** an interview bench task shows no drop in correction turns or tokens-to-first-pass. That task is pending: `bench/tasks/` has no scripted-answer interview yet.

### 4. delegation: spin up models by task complexity; a strong model judges
**Verdict: kept, made concrete and routed.**
- The Explore override on Haiku handles searches.
- `runner` (Haiku) handles verbose runs; `worker` (Sonnet) makes bounded edits.
- `judge` (Opus) reviews only at checkpoints.
- `/hetz-crew:delegate` orchestrates all of it from the main session.
- The main model is chosen at session start and is never switched mid-session.

**Why:**
- Subagents are not cheaper by default. They start cold at 24.5k–46k tokens with a 5-minute cache [SUB-1, SUB-2], and teams use ~7x [SUB-5].
- They pay off when bulky output stays out of an expensive parent and a cheaper model does the legwork [SUB-6]. Hence the 5k-token rule.
- A judge after every step would re-read everything, so it runs once per change.
- minimal-footprint's removal of the judge was reverted: the judge is what makes a Sonnet main session safe, and it is core to your idea.
- `/model` mid-session rebuilds the cache [CACHE-1]. Model routing therefore lives in subagent definitions, not switches.

**Revisit if:** the bench shows delegate costing more than plain implementation at an equal pass rate for a task class. Then that class gets the inline route.

### 5. ignore: add every unnecessary thing to .claudeignore
**Verdict: rejected as specified.** `.claudeignore` has no effect; it is absent from the 2.1.272 binary [IGN-1].

**Instead:**
- `CLAUDE_CODE_GLOB_NO_IGNORE=false`, so Glob stops returning gitignored files [IGN-5].
- A tiny `//`-anchored global deny list for minified and map files [IGN-4].
- Per-repo deny candidates from `/hetz:slim`, written to `.claude/settings.local.json` by default.

**Why:**
- Grep already respects `.gitignore`; Glob does not, and that is the real gap [IGN-5].
- A Read deny also blocks Edit and Write [IGN-2], and doesn't stop `grep -r` or scripts [IGN-3].
- So "every unnecessary thing" (lockfiles, snapshots, node_modules) would break legitimate work, such as resolving a lockfile conflict. minimal-footprint's global lockfile deny was dropped for that reason.
- Its bare-name user-scope patterns were replaced with `//`-anchored ones.

**Revisit if:** the ledger shows repeated large reads of a path class that is never edited. Add it per repo.

### 6. readme: long instruction files get re-read every turn
**Verdict: premise corrected, goal kept** in `/hetz:slim`:
- root CLAUDE.md ≤60 lines
- path-scoped rules
- HTML comments for maintainer notes
- skills for procedures
- a `# Compact instructions` stub
- a MEMORY.md diet

**Why:**
- CLAUDE.md is not re-read from disk each turn. Root files load at session start and after compaction; nested CLAUDE.md files and path-scoped rules load once, when Claude touches a matching file. Whatever is loaded is re-sent with every request (more often than every turn) at the cache-read rate, and loaded into most subagents [MEM-1, MEM-2].
- Edits apply only after `/clear` or `/compact`, and `@imports` save nothing [MEM-3].
- The dollar effect is modest (~$0.50 per 500 requests for 5k tokens on Opus 5.5) [MEM-5]. Adherence is the stronger reason to trim.
- An automatic LLM rewrite of CLAUDE.md (caveman-compress) was rejected. Diff-and-confirm only.

**Revisit if:** never for the premise. Trim targets may change with bench data.

### 7. rotate sessions: a skill that clears or compacts
**Verdict: changed.** Skills cannot run `/clear` or `/compact` [CTX-8], so rotation is mechanised around you:
- `autoCompactWindow` 200000 as a hard cap.
- A tuned native idle nudge [CTX-7].
- Session-guard messages for size thresholds, commit boundaries, cold resumes and model switches.
- The statusline meter.
- `/hetz:handoff` plus an automatic one-time pickup in the next session.

**Why:**
- Context growth is the largest measured waste. In the biggest measured session, 90% of the cost came from requests above 300k context [COST-8].
- A settings cap bounds the worst case, and a free `/clear` plus a handoff note beats any instruction text.
- 1M-window models otherwise compact only near 967k [CTX-5].
- workflow-ux omitted the cap, which was its largest gap.

**Revisit if:** the bench shows a different cap (150k–300k) is cheaper at an equal pass rate.

### 8. verbose terminal output: quiet flags and custom filters
**Verdict: kept, split across the cheapest layers:**
- `bashOutputMaxChars` (setting) caps everything [OUT-1].
- Quiet env vars go to `CLAUDE_ENV_FILE` from a SessionStart hook, so they reach Bash only.
- squeeze filters noisy successful output and appends a one-line quiet-flag tip.
- [quiet-flags.md](quiet-flags.md) is the human cheat sheet.
- `runner` absorbs truly long runs.

**Why:**
- Bash produced 52% of your tool-result characters [COST-9].
- Env vars work without the model remembering anything, and the tip teaches at the moment of waste.
- A "quiet flags" rule block would be billed on every request in every repo.

**Revisit if:** any KEEP line is ever lost, or 14+ days of real dev sessions show squeeze rarely fires (`scripts/hetz_ledger.py --replay-squeeze --since 14d`, plus the live `squeeze.tsv` rows). First replay (2026-10-17, 60d window, only 4 sessions, mostly research): 0 of 2,232 successful Bash results lost a KEEP line, but just 5 were squeezable, so 0.19% of Bash chars were removed. The 15% share gate cannot be judged on that history: 80% of its Bash chars are python/sed/grep/cat dumps that squeeze leaves alone by design. As shipped it runs unless `HETZ_SQUEEZE=0`; `# hz:raw` skips it for one command, and every rewrite keeps the raw log.

## Other proposals rejected or changed

| # | Proposal (origin) | Decision | Why |
|---|---|---|---|
| 1 | Quiet-flag rules as always-on text / a house-rules block in `~/.claude/CLAUDE.md` | Rejected (bench arm only) | Always-on text is billed on every request in every repo and can't be toggled per plugin. It graduates only if its ablation pays for its ~100 tokens. |
| 2 | Quiet-tool env vars (NO_COLOR, npm_config_*, PYTEST_ADDOPTS) in settings `env` (minimal-footprint) | Moved to `CLAUDE_ENV_FILE` via SessionStart | Settings env also reaches the Claude Code process (NO_COLOR can strip its UI colours). Plugin-delivered env toggles with the plugin. PYTEST_ADDOPTS and CI=1 change tool behaviour, so they are only in `HETZ_QUIET_ENV=aggressive`. |
| 3 | Output coach injecting PostToolUse `additionalContext` (workflow-ux) | Merged into squeeze's footer tip | Extra context adds tokens without removing the waste. A tip on output that was just shrunk is net-negative. |
| 4 | PreToolUse command rewriter, like the costs-page example that pipes tests into `grep`/`head` | Rejected | The pipe masks the exit code, a passing run comes back empty, and `allow` skips permission prompts [OUT-5]. Safe matching means re-implementing permission parsing. PostToolUse replacement leaves the command untouched. |
| 5 | PreModelSwitch `ask` decision (max-savings) | Non-blocking `systemMessage` | The decision shape is unverified on 2.1.272, and friction on every deliberate switch is poor UX. Showing the dollar cost at the moment of switching captures most of the value. |
| 6 | `hz-run` PATH wrapper, lean output style, separate review/cost/deny-noise skills (max-savings) | Merged or dropped | `runner` absorbs the log-to-file pattern; Concise covers lean; the judge covers review; the ledger is a terminal script (0 tokens); deny-noise is part of `/hetz:slim` and `/hetz:setup repo`. |
| 7 | `hetz-lab` plugin with an experimental terse style and trim hook (workflow-ux) | Bench arms instead | Fewer install surfaces; squeeze already contains the trim logic behind a gate. |
| 8 | `model:`/`effort:` keys on main-thread skills (e.g. `/delegate` on Opus); `opusplan` | Rejected | Each is a model switch that rebuilds the whole cached prefix, 25–40x a warm turn at 150k [CACHE-1, CACHE-5, COST-5]. Models are pinned only on subagents or chosen at session start. |
| 9 | One model-invocable skill per idea | Rejected | Every model-invocable description is billed on every request in every repo [MEM-6], and `skillOverrides` can't silence plugin skills. The result: 5 user-invoked skills, 0 listing tokens in `hetz`, and only ~70–80 tokens of crew descriptions (under the 80 budget; `claude plugin details hetz-crew` shows the current figure) in a separately toggleable plugin. |
| 10 | Global `CI=1`, `MAX_THINKING_TOKENS=0`, `DISABLE_NON_ESSENTIAL_MODEL_CALLS`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | Rejected (`CI=1` only in aggressive quiet env) | CI=1 changes behaviour (jest snapshots, warnings as errors). Thinking can't be disabled on the 5.5 models [MOD-2]. The third doesn't exist [MISC-5]. The fourth also kills updates, telemetry and the advisor. |
| 11 | Proxies and context packers: context-mode, headroom, pxpipe, claude-code-router, caveman Auto, repomix, claude-mem | Rejected as defaults | They are invasive or restrictively licensed (context-mode is ELv2), carry fidelity risk, or add context. A base-URL proxy loads every MCP schema upfront [MISC-4]. Their savings figures are self-reported. |
| 12 | Worktrees, parallel sessions and agent teams as savers; batch-edit, grep-before-read and re-read-dedupe skills | Rejected | Parallelism multiplies tokens [SUB-5]. Ranged reads, Grep `head_limit`, parallel tool calls and read dedupe are already native. |
| 13 | Global lockfile and build-dir deny (minimal-footprint) | Dropped | Read deny blocks Edit and Write [IGN-2]. Per-repo candidates come from `/hetz:slim` instead. |
| 14 | Always-on text in `~/.claude/CLAUDE.md` (workflow-ux) | Rejected | Same cost as #1. hetz writes nothing there. |
| 15 | Stage-2 lossy trimming on by default (max-savings) | Gated | It runs only when output is still >80 lines after lossless cleanup, never drops KEEP lines, and always saves the raw log. squeeze itself runs by default (`HETZ_SQUEEZE=0` turns it off); the replay gate decides whether that stays. |
| 16 | Force-for-plugin output style, ~220 tokens (max-savings) | Dropped | It overrides your own `outputStyle` in every repo. |
| 17 | Slash-command launchers (`cc-spec`, `cc-do`) | Opt-in, unverified | They ship in the optional launchers item. Whether an initial-prompt slash command executes on 2.1.272 is still an [open item](EVIDENCE.md#open-verification-items-21272); if it doesn't, type the command after launch. |

## Clarified while writing these docs (2026-10-17)

- **Built-in Explore's model.** The research was inconsistent ("Opus alias" vs "inherit"). The binary and docs agree: Explore inherits the main model, capped at Opus only when the main model is Fable on first-party auth [SUB-3]. On an Opus main, every Explore run is Opus, which strengthens the case for the Haiku override.
- **Idle-nudge defaults** confirmed in the binary: 75 minutes and 100k tokens [CTX-7]. hetz's profile tightens them to 60 min (5 on API keys) and 50k.
- **RTK on macOS** reads `~/Library/Application Support/rtk/config.toml`, not `~/.config/rtk/` [RTK-8].
- **RTK full edition vs rtk-oss.** The full edition can add a PostToolUse compression pipeline and an API proxy. The first would collide with squeeze; the second sets a base URL that loads all MCP schemas upfront [MISC-4]. `rtk-oss` has neither [RTK-5], which is one more reason to prefer it.
- **`--bare` needs API-key auth.** On a subscription, scripted runs use `--max-budget-usd` and `--max-turns` instead [MISC-3].
- **Squeeze default.** DESIGN planned `HETZ_SQUEEZE=0` until the replay gate passed. The shipped hook runs unless `HETZ_SQUEEZE=0` is set, and these docs describe that. Set it yourself to stay off until your own replay (`scripts/hetz_ledger.py --replay-squeeze`) clears the gate.
- **Explore override precedence** confirmed in the binary: agents merge by name in the order built-in, plugin, user, project, flag, policy, so a user or project `Explore` replaces the built-in [SUB-3].
- **User-scope deny anchoring.** The permissions doc anchors `/path` at `~/.claude/` in user settings and treats bare names as relative to the current directory. hetz keeps `//**/…` patterns because they are unambiguous [IGN-4].
