# hetz evidence

<!-- hetz:evidence-version 2.1.272 -->
Verified against **Claude Code 2.1.272** on **2026-10-17**. Every row applies to that version and date unless it says otherwise.
Docs are newer than the binary: features documented for later versions are flagged, not used.

**Confidence.** H = stated by the primary source or confirmed in the binary. M = inferred, measured once, or from a truncated extract. L = third-party or self-reported.

**Checked.** `bin` = string or code found in the 2.1.272 binary today. `doc` = official doc page. `cli` = `claude --help`. `local` = a measured developer's transcripts. `calc` = arithmetic on the price table. `repo` = the third-party project's own files.
Source keys are listed under [Sources](#sources). Docs are paraphrased; re-read the page before building on exact wording.

## Cost model and prices

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| COST-1 | Every request re-sends the whole conversation; each tool round-trip is another request, so a one-line question in a long session pays for the whole context. | [costs] "Why usage climbs" | H | doc |
| COST-2 | List price per MTok (input / 5m write / 1h write / cache read / output): Opus 5.5 $4 / $5 / $8 / $0.20 / $20 · Sonnet 5.5 $2 / $2.50 / $4 / $0.10 / $10 · Haiku 5.5 $0.10 / $0.125 / $0.20 / $0.01 / $0.50 (prompts up to 100k) · Opus 5 and 4.5–4.8 $5 input, $0.50 read, $25 output. | [pricing] | H | doc |
| COST-3 | Cache read = 0.05x input on Opus 5.5 and Sonnet 5.5, 0.1x on most other models. 5m write = 1.25x, 1h write = 2x. Output = 5x input = 100x a cache read on the 5.5 models. | [pricing] | H | doc |
| COST-4 | Haiku 5.5 costs 5x more per token once a prompt exceeds 100k tokens. | [pricing] | H | doc |
| COST-5 | At 150k context on Opus 5.5 a warm request costs ~$0.03; a cache rebuild costs ~$0.75 (5m) or ~$1.20 (1h): 25–40x. | COST-2 | H | calc |
| COST-6 | Per warm request on Opus 5.5: 50k ≈ $0.01, 200k ≈ $0.04, 600k ≈ $0.12. Context size is a per-request multiplier. | COST-2 | H | calc |
| COST-7 | Measured spend (one developer's history) at list price: 68% cache reads, 25% cache writes, ~7% output. By tokens: 98.1% / 1.7% / 0.2%. (14 transcripts, 1,142 requests; output may be under-recorded, so 7% is a floor.) | [local] | M | local |
| COST-8 | Largest measured session: Sonnet 5, 698 requests. 63% of its cost came from requests above 600k context, 90% from above 300k; peak 875k. | [local] | H | local |
| COST-9 | Bash produced 52% of a measured developer's tool-result characters (1.69M); 223 of 699 Bash calls returned >2k chars, mostly `cat`/`sed`/`grep` file dumps. | [local] | M | local |
| COST-10 | Opus 5.5 is the default model on API, Pro, Max, Team and Enterprise; the costs page names an unchanged Opus default as a common cause of high spend. | [model], [costs] | H | doc |
| COST-11 | Thinking tokens are billed as output. | [costs] | H | doc |

## Prompt cache

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| CACHE-1 | Rebuilds the cached prefix: `/model` switch; effort change (most models); fast mode on; MCP tools added or removed without tool search; enabling a plugin that ships MCP servers; compaction; the image/PDF limit being hit; first session after a Claude Code upgrade; each `opusplan` plan-mode toggle; a skill or command whose frontmatter names another `model`. | [caching] | H | doc |
| CACHE-2 | The binary's cache-miss diagnosis names: system_prompt_changed, tools_changed, model_changed, fast_mode_changed, effort_changed, betas_changed, messages_rewritten, ttl_expired_5m, ttl_expired_1h, likely_server_side. | [bin] | H | bin |
| CACHE-3 | Cache-safe: file edits, CLAUDE.md edits (not applied until reload), permission-mode changes (except with `opusplan`), output-style changes, invoking a skill, `/recap`, `/rewind`, spawning a subagent, toggling the advisor. | [caching] | H | doc |
| CACHE-4 | Changing effort keeps the cache on Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.1 with an API key or subscription; not on Bedrock, Agent Platform, a Claude apps gateway, or with experimental betas disabled. | [caching] | H | doc |
| CACHE-5 | A skill naming a different `model` is a switch for that turn, and the session model resumes on the next prompt, so expect two rebuilds. | [caching]; second rebuild inferred | H / M | doc |
| CACHE-6 | TTL: main conversation 1h on a subscription within plan usage; 5m once drawing usage credits, on API keys and on cloud providers. Subagents and workflows: 5m by default, even on a subscription. | [costs], [caching] | H | doc |
| CACHE-7 | The statusline JSON carries a `prompt_cache` object with `last_miss_cause`, `miss_causes` and `recache_tokens_if_cold`; `/usage` shows a "Prompt cache (main)" line (v2.1.251+). | [bin], [costs] | H | bin, doc |
| CACHE-8 | Cache scope is effectively per machine and directory: sessions in the same directory and git snapshot share prefixes. | [caching] | H | doc |

## Context, compaction, rotation

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| CTX-1 | `/clear` costs nothing (aliases `/reset`, `/new`). `/compact` reads the whole conversation: cheap while warm, full write price when cold. | [costs], [caching] | H | doc |
| CTX-2 | `/rewind` (aliases `/checkpoint`, `/undo`) truncates to an already-cached prefix, so it is cheaper than compacting when abandoning a path. | [caching], [bin] | H | doc, bin |
| CTX-3 | `/recap` appends a one-line summary without rewriting history, so the cache survives. | [caching], [bin] | H | doc, bin |
| CTX-4 | `/btw` asks a side question whose answer does not enter the conversation. Whether it sends a full-context request is not documented. | [best], [bin] | H / M | doc, bin |
| CTX-5 | Models with a native 1M window (Sonnet 5+, Opus 4.7+, Haiku 5.5, Fable) auto-compact only near ~967k by default. | [model] | H | doc |
| CTX-6 | The compaction window is set by the `autoCompactWindow` setting (100k–1M), `/autocompact`, or `--autocompact`; `CLAUDE_CODE_AUTO_COMPACT_WINDOW` overrides all three. | [model], [env], [bin], [cli] | H | doc, bin, cli |
| CTX-7 | The native idle nudge ("new task? /clear") fires after `CLAUDE_CODE_IDLE_THRESHOLD_MINUTES` (default 75) idle minutes when context is at least `CLAUDE_CODE_IDLE_TOKEN_THRESHOLD` (default 100000). | [bin] | H | bin |
| CTX-8 | Skills cannot run `/clear` or `/compact`; only a few built-ins (e.g. `/init`) are reachable through the Skill tool. | [skills] | H | doc |
| CTX-9 | After compaction the system prompt, output style, root CLAUDE.md, unscoped rules, auto memory, the plan file, up to 5 recently edited files and invoked skill bodies (5k tokens each, 25k total) survive. Path-scoped rules and nested CLAUDE.md drop until re-triggered; skill descriptions and chat-only instructions are lost. | [ctxwin] | H | doc |
| CTX-10 | A resumed session past its TTL reprocesses its full history. The SessionStart hook input carries `prompt_cache_likely_expired` and `estimated_cache_write_usd`. | [caching], [hooks], [bin] | H | doc, bin |
| CTX-11 | Autocompact fires mid-task; running `/compact` yourself at a break chooses when the overhead lands. | [caching] | H | doc |

## Always-loaded context

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| MEM-1 | CLAUDE.md is not re-read every turn. The InstructionsLoaded `load_reason` is one of session_start, nested_traversal, path_glob_match, include, compact: root files load at session start and after compaction, nested CLAUDE.md files and path-scoped rules load lazily when Claude touches a matching file, and `@` includes load with their parent. Edits to a loaded file apply after `/clear`, `/compact` or restart. | [memory], [hooks], [bin] | H | doc, bin |
| MEM-2 | CLAUDE.md is sent with every request (cached project-context layer, as a user message after the system prompt), and is loaded into general-purpose and custom subagents. Explore, Plan and agents with `omitClaudeMd` skip it. | [memory], [sub] | H | doc |
| MEM-3 | `@path` imports expand at launch, so they save nothing. Block-level HTML comments are stripped before injection. `.claude/rules/*.md` with `paths:` load only when a matching file is touched. Target under 200 lines. | [memory] | H | doc |
| MEM-4 | Auto memory loads the first 200 lines or 25 KB of `MEMORY.md` every session. | [memory] | H | doc |
| MEM-5 | A 5k-token CLAUDE.md over 500 requests is 2.5M cache-read tokens, ~$0.50 on Opus 5.5, multiplied by each subagent that loads it. Adherence is the stronger reason to trim. | MEM-2, COST-2 | M | calc |
| MEM-6 | Skill and agent descriptions sit in context every request. `disable-model-invocation: true` removes a skill's description. The listing budget is 1% of the context window in characters (`skillListingBudgetFraction` 0.01); each entry is capped at 1,536 chars. | [skills], [bin] | H | doc, bin |
| MEM-7 | `claude plugin details <name>` prints a plugin's always-on tokens; hooks are reported as harness-only with no model-context cost. | [measure] | H | doc, cli |
| MEM-8 | Plain hook stdout enters context only on UserPromptSubmit, UserPromptExpansion, SessionStart and PostModelSwitch; `systemMessage` is shown to the user, not the model. | [hooks] | H / M | doc |

## Excluding files

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| IGN-1 | `.claudeignore` has no effect. The permissions doc says to move its entries into Read deny rules; the binary contains no `claudeignore` string in any case. | [perm], [bin] | H | doc, bin |
| IGN-2 | Read deny rules use gitignore syntax and also block Edit and Write on the path, including creating files there (v2.1.208+ edits, v2.1.228+ writes). | [perm] | H | doc |
| IGN-3 | Read deny covers `cat`, `head`, `tail`, `sed`, `tee` and redirections in Bash, but not `grep -r` from a parent directory or scripts that open files themselves. OS-level blocking needs the sandbox. | [perm] | H | doc |
| IGN-4 | In user settings `/path` anchors at `~/.claude/`; to apply in every project use `//` (filesystem root) or `~/`. Bare names match under the current directory; hetz uses `//` anyway because it is unambiguous. | [perm] | H / M | doc |
| IGN-5 | The Grep tool runs ripgrep with `--hidden` and without `--no-ignore`, so it respects `.gitignore`. The Glob tool adds `--no-ignore --hidden` unless `CLAUDE_CODE_GLOB_NO_IGNORE=false`. Read-deny patterns are passed to ripgrep as `--iglob`. | [bin] | H | bin |
| IGN-6 | `respectGitignore` only affects the @ file picker. | [bin], [settings] | H | bin |

## Output caps and filtering

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| OUT-1 | `bashOutputMaxChars`: chars of *successful* Bash output Claude receives inline. Default 30000, clamped to 4000–128000; overflow is saved to a file plus a short preview; also replaces `BASH_MAX_OUTPUT_LENGTH`. Added in v2.1.261. | [bin], [settings] | H | bin |
| OUT-2 | Failed Bash commands show ~10k chars inline as a head-and-tail excerpt, with no file path. | [tools] | H | doc |
| OUT-3 | Read returns at most 25k tokens per call (`CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`); MCP results cap at 25k tokens (`MAX_MCP_OUTPUT_TOKENS`). | [tools], [env] | H | doc; names in bin |
| OUT-4 | A PostToolUse hook's `updatedToolOutput` replaces what Claude sees and must match the tool's output shape. The exact Bash shape on 2.1.272 is unverified. | [hooks] | H / M | doc |
| OUT-5 | A pipe masks the producer's exit code (`cmd \| head` exits with head's status) unless `set -o pipefail`. The costs page's example test-filter hook has this flaw and also auto-allows the command. | [costs]; shell semantics | H | doc |
| OUT-6 | Claude's Bash tool runs bash or zsh, never fish (`CLAUDE_CODE_SHELL`), so `set -o pipefail` is available. | [env] | M | doc |

## Subagents and delegation

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| SUB-1 | Subagent first-request size measured here: Haiku 5.5 24.5k, Haiku 4.5 13.8k, Sonnet 5 ~39.5k, Opus 5.5 ~46k tokens. Parallel siblings shared a 34.6k cached prefix. | [local] | M | local |
| SUB-2 | Cache writes were 38.6% of subagent cost vs 23.5% of main-session cost (cold starts and the 5m TTL). | [local] | M | local |
| SUB-3 | Built-in Explore runs on the main conversation's model (definition `model:"inherit"`, capped at Opus when the main model is Fable on first-party auth) and skips CLAUDE.md. A user or project agent named `Explore` overrides it with its own `model`: agents merge by name in the order built-in, plugin, user, project, flag, policy, last wins. | [sub], [bin] | H | doc, bin |
| SUB-4 | `CLAUDE_CODE_SUBAGENT_MODEL` changes general-purpose subagents, not Explore or Plan. | [sub] | H | doc |
| SUB-5 | Agent teams use ~7x the tokens of a standard session (teammates in plan mode); Anthropic's multi-agent research system used ~15x a chat. | [costs], [multiagent] | H | doc |
| SUB-6 | A 50k-token log inline in an Opus 5.5 parent costs ~$0.25 to write at the 5m rate or ~$0.40 at the 1h rate (a subscription main thread, CACHE-6), plus ~$0.01 per later request (~$0.55 / ~$0.70 over 30 turns); a Haiku runner costs ~$0.01–0.02 and returns ~300–800 tokens. A 3k-token task inline costs ~$0.08, less than an Opus subagent's cold start (~$0.23). | SUB-1, COST-2, CACHE-6 | M | calc |
| SUB-7 | The advisor (`/advisor opus`, `advisorModel`) is experimental and Anthropic-API only; each call reads the full transcript at advisor rates; toggling it keeps the main cache. | [advisor] | H | doc |
| SUB-8 | Output styles do not apply to subagents, except forks. | [styles], [sub] | H | doc |
| SUB-9 | The text you send to a subagent is output at the parent's price. | COST-3 | H | calc |

## Models and effort

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| MOD-1 | Default effort is `medium` on Opus 5.5, Sonnet 5.5 and Haiku 5.5; `high` elsewhere. | [model] | H | doc |
| MOD-2 | Thinking cannot be turned off on Opus 5.5, Sonnet 5.5, Haiku 5.5 or Fable; adaptive models ignore `MAX_THINKING_TOKENS`. Lower effort instead. | [model], [env] | H | doc |
| MOD-3 | Sonnet 5.5 is 2x cheaper than Opus 5.5 per token; Haiku 5.5 is 40x cheaper on input. | COST-2 | H | calc |

## Output style and caveman

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| STY-1 | A built-in `Concise` output style exists (v2.1.237+): lead with the result, skip preamble and narration, keep full detail for errors and explicit requests. It lives in the cached system prompt plus a short per-turn reminder. | [styles], [bin] | H | doc, bin |
| STY-2 | caveman's own eval (Opus 5.5, 10 questions, n=1 per arm): no instruction 6,983 output tokens; "Answer concisely." 4,334; `/caveman` 4,119 (−3% vs concise); `/ultracave` 2,693. | [caveman] | H that it is reported; M as evidence | repo |
| STY-3 | caveman's rules cost ~1,160 input tokens per session plus a per-turn reminder (~85 tokens, estimated from its string). | [caveman] | M | repo |
| STY-4 | JetBrains A/B (86 tasks, Sonnet 5 at low effort): −8.5% output, quality flat (8 better / 10 worse / 64 tied). Total arm cost rose 11.6% because one task crossed the 200k long-context tier; an earlier 10-task −29.5% did not replicate. | [jetbrains] | M | one research pass read it; another got HTTP 403 |
| STY-5 | With output at ~7% of spend (COST-7), an 8.5% output cut is under 1% of the bill. | COST-7, STY-4 | M | calc |
| STY-6 | Compressing the *input* prompt raised cost ~1.15x and hurt accuracy in Adobe's study; concise chain-of-thought hurt math on weak, non-thinking models. Brevity is safest when reasoning happens in hidden thinking. | [cavewoman], [renze] | H for abstracts | doc |

## RTK

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| RTK-1 | RTK (Rust Token Killer) is a binary plus a PreToolUse hook that rewrites Bash commands (`git status` → `rtk git status`). It never sees Read, Grep or Glob. | [rtk] README | H | repo |
| RTK-2 | Its 60–90% figure measures shell-output bytes only, with tokens estimated as bytes/4; RTK's own docs say the saving shrinks against total input and again against cost. | [rtk] savings-explained | H | repo |
| RTK-3 | Open issues where failures can look green or exit codes are masked: #4421 (rspec, phpunit, mvn, bun, deno, ctest, sbt), #3267 (diff), #2974 (pnpm/eslint). Fidelity issues: #3434 (`ls` hides build dirs), #3411, #4364 (first command in a pipe rewritten). | [rtk] issues | H filed / M severity | repo |
| RTK-4 | Issue #3492 reports RTK defaults 16% more expensive per passing run (agent chased phantom failures); a contributor could not reproduce on v0.48.0. Disputed. | [rtk] #3492 | L–M | repo |
| RTK-5 | License: CLI Apache-2.0; the default binary links `rtk-engine` (BUSL-1.1). `rtk-oss` is Apache-2.0 only and omits `rtk pipeline`, PostToolUse compression and delegation. `rtk-proxy` is a separate BUSL-1.1 daemon. | [rtk] INSTALL, README | H | repo |
| RTK-6 | 1.0.0 shipped 2026-10-13, 1.0.1 on 2026-10-14; ~83.7k stars; ~886 open issues. | [rtk] via gh | H | repo |
| RTK-7 | `cargo install rtk` from crates.io can install an unrelated "Rust Type Kit"; `rtk gain` tells them apart. | [rtk] INSTALL | H | repo |
| RTK-8 | Config lives at `~/Library/Application Support/rtk/config.toml` on macOS and `~/.config/rtk/config.toml` on Linux; `[hooks] exclude_commands` never rewrites matching commands (prefix or `^regex`, env prefixes stripped). | [rtk] configuration.md | H | repo |
| RTK-9 | RTK's recall store keeps full output of failed commands on disk (may include secrets, #1875) and its tracking DB stores full command lines (#1160). | [rtk] issues | H | repo |

## Background spend

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| BG-1 | Scheduled tasks and `/loop`, cross-session messages and goal check-ins each start a turn that sends the full context, even while you are idle. `CLAUDE_CODE_GOAL_CHECKIN_MINUTES=0` stops check-ins; `crossSessionInbound: hold` holds messages. | [costs] | H | doc |
| BG-2 | Prompt suggestions add one mostly-cached request per turn; `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false` turns them off. | [costs], [bin] | H | doc, bin |
| BG-3 | Background summarisation and checks are typically under $0.04 per session. | [costs] | H | doc |

## Misc

| ID | Claim | Source | Conf | Checked |
|---|---|---|---|---|
| MISC-1 | An image costs ⌈w/28⌉ × ⌈h/28⌉ tokens; a 1920×1080 screenshot is ~2,691 tokens. Many images eventually force a batch removal and a partial cache rebuild. | [vision], [caching] | H | doc |
| MISC-2 | Vague requests trigger broad scanning; naming the file and function keeps reads minimal. | [costs] | H | doc |
| MISC-3 | `--bare` skips hooks, LSP, plugin sync, auto-memory and CLAUDE.md discovery, and authenticates only with `ANTHROPIC_API_KEY` or `apiKeyHelper` (no subscription OAuth). `--max-budget-usd` caps a scripted run. | [cli] | H | cli |
| MISC-4 | Pointing `ANTHROPIC_BASE_URL` at a third-party proxy loads all MCP tool definitions upfront unless `ENABLE_TOOL_SEARCH=true`. | [env], [costs] | H | doc |
| MISC-5 | `DISABLE_NON_ESSENTIAL_MODEL_CALLS` is not in the binary. `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` exists but also disables telemetry, auto-updates and feature-flag fetches (which the advisor needs). | [bin], [env] | H / M | bin, doc |
| MISC-6 | Prefer CLIs (`gh`, `aws`) over MCP servers; MCP schemas are deferred by default on supported models; `/context all` shows per-tool cost. | [costs], [env] | H | doc |
| MISC-7 | The official best practices recommend letting Claude interview you (AskUserQuestion), writing a spec, then implementing in a fresh session; plan mode is the built-in lighter version. | [best] | H | doc |
| MISC-8 | Docs describe features newer than 2.1.272: `idleCompaction` (2.1.290; absent from the binary), `/doctor prompt-audit` (2.1.283), Read `allow_large` and agent `autoCompactWindow` (2.1.296). hetz uses none of them. | [settings], [sub], [bin] | H | doc, bin |

## Myths

| Myth | Reality | Evidence |
|---|---|---|
| `.claudeignore` hides files from Claude. | It does nothing: zero mentions in the binary, and the docs say so. Use `Read(...)` deny rules plus `CLAUDE_CODE_GLOB_NO_IGNORE=false`. | IGN-1, IGN-5 |
| CLAUDE.md is re-read from disk every turn. | The root file is loaded at session start and re-sent from memory with every request at the cache-read rate; nested files and path rules load once, when a matching file is touched. Edits apply only after `/clear` or `/compact`. Trimming helps mostly through adherence. | MEM-1, MEM-2, MEM-5 |
| `@imports` keep CLAUDE.md small. | They expand at launch. Path-scoped rules and skills are what defer text. | MEM-3 |
| Delegating to subagents is automatically cheaper. | A subagent starts cold at 24–46k tokens with a 5-minute cache; teams use ~7x. It pays only when bulky output stays out of an expensive parent, or a cheaper model does the legwork. | SUB-1, SUB-5, SUB-6 |
| The built-in Explore agent is a cheap helper. | It runs on your main model (Opus main → Opus Explore). Override it with a user agent named `Explore` on `haiku`. | SUB-3, SUB-4 |
| caveman cuts tokens 65–75%. | Its own eval shows −3% output beyond "Answer concisely." Output is ~7% of a measured developer's spend, so the bill effect is under 1%, against ~1.2k rule tokens. | STY-2, STY-3, STY-5 |
| `DISABLE_NON_ESSENTIAL_MODEL_CALLS` stops background calls. | Not in 2.1.272. The real switch also kills updates and telemetry. | MISC-5 |
| `MAX_THINKING_TOKENS=0` turns thinking off. | Not on the 5.5 models. Lower effort instead. | MOD-2 |
| RTK saves 60–90% of tokens. | Of shell-output bytes. Bill impact is smaller and disputed. | RTK-2, RTK-4 |
| A skill can rotate the session for you. | Skills cannot run `/clear` or `/compact`. hetz nudges; you type the command. | CTX-8 |
| `/compact` is a free reset. | `/clear` is free. `/compact` re-reads the whole conversation and costs the full write price when cold. | CTX-1 |
| Switching to a cheaper model mid-session saves money. | It rebuilds the whole cache (~25–40x a warm turn at 150k). Choose the model at session start. | CACHE-1, COST-5 |
| Any effort change busts the cache. | Not on the 5.5 models with an API key or subscription. Model switches still do. | CACHE-4 |
| Read deny makes a file invisible. | It also blocks Edit/Write, and does not stop `grep -r` or scripts. | IGN-2, IGN-3 |
| `respectGitignore` keeps ignored files out. | It only affects the @ picker. Grep already respects `.gitignore`; Glob does not by default. | IGN-5, IGN-6 |
| `CLAUDE_CODE_SUBAGENT_MODEL=haiku` makes exploration cheap. | It does not touch Explore or Plan. | SUB-4 |
| Worktrees, parallel sessions and agent teams save tokens. | They save wall-clock time and multiply tokens. | SUB-5 |
| Output tokens are the main cost. | Output is ~7% of a measured developer's list-price spend; context re-reads and rebuilds are ~93%. | COST-7 |

## Ledger baseline (before hetz)

From the research pass over a measured developer's `~/.claude/projects/**/*.jsonl` (usage numbers only), 2026-10-17. Reproduce and save a snapshot with `scripts/hetz_ledger.py --since 30d --json > docs/ledger-baseline.json`.

| Metric | Value | ID |
|---|---|---|
| Spend split, list price | cache read 68% · cache write 25% · output ~7% | COST-7 |
| Largest session | 698 requests, peak 875k context | COST-8 |
| Cost share from requests >300k / >600k context (largest session) | 90% / 63% | COST-8 |
| Bash share of tool-result chars | 52% (1.69M chars); 223 of 699 calls >2k chars | COST-9 |
| Main-session start size | 39–65k tokens | [local] |
| Subagent cold start | Haiku 5.5 24.5k · Opus 5.5 ~46k tokens | SUB-1 |

## hetz's own measurements

Not measured yet. The bench (`bench/`) fills this table with k, model, effort and Claude Code version; until then every saving in the docs is an estimate.

| Component | Metric | Result | k / model / effort / CC version | Date |
|---|---|---|---|---|
| Suite D vs A | median total_cost_usd Δ, pass rate | pending | | |
| autoCompactWindow 200k | cost share >300k | pending | | |
| squeeze | Bash chars removed (replay), KEEP lines lost | pending | | |
| return contract | parent tokens after Explore | pending | | |
| crew + Explore override | cost on repo-qa, noisy-suite | pending | | |
| handoff + /clear | three-tasks cost vs one thread (arms D-clear, D-handoff) | pending | | |

## Open verification items (2.1.272)

Each has a fallback in docs/DESIGN.md; mark the result here when checked.

- PreModelSwitch hook fires and accepts a `systemMessage`.
- Resolved 2026-10-17 (real 2.1.272 CLI against a local mock API, both plugins via `--plugin-dir`): squeeze's `updatedToolOutput` replaces a successful `make` result (1501→3 lines, warning kept, raw log written); `git diff` (605 lines) and `cat` pass through untouched. A non-zero exit fires no PostToolUse: the model got Claude Code's own error result, cut to its first ~10k chars, so a trailing `error:` line was not shown. With `|| true` the same build is squeezed and the error line kept.
- Resolved 2026-10-17 (same mock run): `CLAUDE_ENV_FILE` from the plugin SessionStart hook reaches Bash (`PAGER=[cat] NO_COLOR=[1] PIP_PROGRESS_BAR=[off]`). The SubagentStart contract reached an Explore spawn's first user message, and a Stop-hook `systemMessage` (commit boundary) was shown as a `Stop says:` notice.
- Top-level `autoCompactWindow` is honored (`/context` shows the window).
- `//`-anchored user-scope deny rules apply in every project.
- Resolved 2026-10-17: a user-scope `~/.claude/agents/Explore.md` overrides the built-in (binary merge order and the sub-agents doc, SUB-3). `/agents` showing Explore (user) remains a cheap live smoke test.
- Partly resolved 2026-10-17: `claude -p "/hetz:handoff"` expands the skill and runs its `!` preamble under the skill's own `allowed-tools`. The interactive form used by `cc-spec`/`cc-do` (`claude "/hetz:interview …"`) is still unchecked.

## Re-verify after an upgrade

Each count is from 2.1.272; a change means the claim needs a fresh look. Replace the version in the path.

```sh
grep -a -c -i claudeignore ~/.local/share/claude/versions/2.1.272                          # 0      IGN-1
grep -a -c DISABLE_NON_ESSENTIAL_MODEL_CALLS ~/.local/share/claude/versions/2.1.272        # 0      MISC-5
grep -a -c -F 'CLAUDE_CODE_GLOB_NO_IGNORE||"true"' ~/.local/share/claude/versions/2.1.272  # 1      IGN-5
grep -a -c -F 'CLAUDE_CODE_IDLE_THRESHOLD_MINUTES??75' ~/.local/share/claude/versions/2.1.272  # 1  CTX-7
grep -a -c -F 'model:"inherit",omitClaudeMd:!0' ~/.local/share/claude/versions/2.1.272    # 2      SUB-3
grep -a -c -F 'load_reason (session_start, nested_traversal, path_glob_match, include, compact)' ~/.local/share/claude/versions/2.1.272  # 1  MEM-1
grep -a -c -F 'Claude receives inline (default 30000' ~/.local/share/claude/versions/2.1.272  # 2  OUT-1
grep -a -c -F 'U.source==="plugin"),s=e.filter((U)=>U.source==="userSettings")' ~/.local/share/claude/versions/2.1.272  # 1  SUB-3
grep -a -c -F 'name:"Concise"' ~/.local/share/claude/versions/2.1.272                     # 1      STY-1
```

## Sources

| Key | Where |
|---|---|
| [costs] | https://code.claude.com/docs/en/costs |
| [caching] | https://code.claude.com/docs/en/prompt-caching |
| [memory] | https://code.claude.com/docs/en/memory |
| [perm] | https://code.claude.com/docs/en/permissions |
| [sub] | https://code.claude.com/docs/en/sub-agents |
| [skills] | https://code.claude.com/docs/en/skills |
| [hooks] | https://code.claude.com/docs/en/hooks |
| [tools] | https://code.claude.com/docs/en/tools-reference |
| [env] | https://code.claude.com/docs/en/env-vars |
| [settings] | https://code.claude.com/docs/en/settings-reference |
| [model] | https://code.claude.com/docs/en/model-config |
| [ctxwin] | https://code.claude.com/docs/en/context-window |
| [styles] | https://code.claude.com/docs/en/output-styles |
| [measure] | https://code.claude.com/docs/en/plugins/measure |
| [advisor] | https://code.claude.com/docs/en/advisor |
| [best] | https://code.claude.com/docs/en/best-practices |
| [pricing] | https://platform.claude.com/docs/en/about-claude/pricing |
| [vision] | https://platform.claude.com/docs/en/build-with-claude/vision |
| [multiagent] | https://www.anthropic.com/engineering/multi-agent-research-system |
| [bin] | `~/.local/share/claude/versions/2.1.272` (JS strings searched 2026-10-17) |
| [cli] | `claude --help` on 2.1.272 |
| [local] | a measured developer's transcripts, usage fields only (14 files, 1,142 requests), research pass 2026-10-17 |
| [caveman] | https://github.com/JuliusBrussee/caveman at commit 81958ad (`evals/README.md`, `docs/HONEST-NUMBERS.md`) |
| [jetbrains] | https://blog.jetbrains.com/ai/2026/07/speak-to-ai-agents-like-cavemen-tosave-tokens/ |
| [cavewoman] | https://arxiv.org/abs/2606.24083 |
| [renze] | https://arxiv.org/abs/2401.05618 |
| [rtk] | https://github.com/rtk-ai/rtk (README.md, INSTALL.md, `docs/guide/resources/savings-explained.md`, `docs/guide/getting-started/configuration.md`, issues as numbered) |
