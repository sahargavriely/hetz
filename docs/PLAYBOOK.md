# hetz playbook: habits that save tokens

Claude Code 2.1.272, 2026-10-17. Hooks and settings already do what can be automated. This page covers the decisions only you can make. Bracketed IDs point to rows in [EVIDENCE.md](EVIDENCE.md).

## 1. The cost model in five lines

- Every request re-sends the whole context, and every tool call is another request. **Cost ≈ context size × requests + cache rebuilds** [COST-1].
- A warm cache read costs 0.05x input on Opus/Sonnet 5.5. A rebuild costs 1.25x (5m TTL) or 2x (1h TTL). At 150k on Opus 5.5 that is ~$0.03 per warm request vs ~$0.75–1.20 per rebuild [COST-3, COST-5].
- Measured bill at list price (one developer's history): 68% cache reads, 25% cache writes, ~7% output. In the largest measured session, 90% of the cost came from requests above 300k context [COST-7, COST-8].
- So, in order: keep context small → don't bust the cache → keep bulky output out of the main thread → settle ambiguity while context is tiny → trim always-loaded text → terse prose last.
- Cache TTL: 1h for the main thread on a subscription; 5m on API keys, on usage credits, and for every subagent [CACHE-6].

| per MTok | input | cache write 5m / 1h | cache read | output |
|---|---|---|---|---|
| Opus 5.5 | $4 | $5 / $8 | $0.20 | $20 |
| Sonnet 5.5 | $2 | $2.50 / $4 | $0.10 | $10 |
| Haiku 5.5 (≤100k prompt; 5x above) | $0.10 | $0.125 / $0.20 | $0.01 | $0.50 |

## 2. Session habits

- **One task per session.** Between unrelated tasks: `/hetz:handoff` if you'll come back, then `/rename <slug>`, then `/clear`. `/clear` is free [CTX-1].
- **Watch the statusline.** Under 80k: fine. 80–150k: finish the task and plan the rotation. Over 150k: handoff and `/clear`. The 200k auto-compact cap is a backstop, not a target.
- **`/compact <focus>` only at a natural break while the cache is warm.** It re-reads the whole conversation. When you only need the conclusions, handoff + `/clear` is cheaper [CTX-1, CTX-11].
- **Abandoning an approach?** `/rewind` (Esc Esc) instead of piling on corrections. After two failed corrections, `/clear` and re-prompt with what you learned [CTX-2].
- **Side question?** `/btw`; the answer stays out of history. `/recap` summarises without breaking the cache [CTX-3, CTX-4].
- **Don't resume a big session after its TTL.** The first request re-writes the whole history (hetz warns with the dollar figure). Start fresh from the handoff note instead [CTX-10].
- **Commit = task boundary.** When hetz says "Task boundary (committed)", that is the cheapest moment to `/clear`.

## 3. Cache hygiene

- Pick the model and effort **before** the first prompt (`cc`, `cc-deep`, `cc-quick`, or `claude --model … --effort …`). Never `/model` mid-session: it rebuilds everything [CACHE-1].
- Avoid `opusplan`: every plan-mode toggle is a model switch. A skill or command with `model:` in its frontmatter is a switch out and back: two rebuilds [CACHE-5].
- Effort changes are cache-safe on the 5.5 models with an API key or subscription, but not on Bedrock or Agent Platform [CACHE-4].
- Add MCP servers and plugins before starting; don't toggle them mid-session [CACHE-1].
- Images cost ⌈w/28⌉×⌈h/28⌉ tokens (1080p ≈ 2.7k), and enough of them forces a rebuild. Crop, or paste the error text [MISC-1].
- CLAUDE.md edits don't bust the cache, but they don't apply until `/clear` or `/compact` either. Batch them [MEM-1].
- Check `/usage` ("Prompt cache (main)": misses and their cause) or the statusline cache % when a session feels expensive [CACHE-7].

## 4. Model, effort and delegation

| Work | Where | Model · effort |
|---|---|---|
| Most coding and fixes | main | Sonnet · medium (`cc`) |
| Architecture, hard debugging, `/hetz:interview` | main | Opus · high (`cc-deep`) |
| Quick Q&A, renames, one-file scripts | main | Haiku · low (`cc-quick`) |
| "Where/how is X?" needing more than 3 reads | Explore | Haiku (hetz override) |
| Test suite, build, install, lint, log triage | `hetz-crew:runner` | Haiku · low |
| Bounded edit with named files and a check | `hetz-crew:worker` | Sonnet · medium |
| Review of a finished change | `hetz-crew:judge` | Opus · high, once per change |

- **The delegation test:** will I need the output, or only the conclusion? Delegate only when you need just the conclusion and the output would exceed ~5k tokens. A subagent starts cold at 24.5k (Haiku) to ~46k (Opus) tokens [SUB-1, SUB-6].
- Built-in Explore runs on *your main model* unless overridden. `CLAUDE_CODE_SUBAGENT_MODEL` doesn't change that [SUB-3, SUB-4].
- Your prompt to a subagent is billed as output at the parent's price, so give paths and commands, not essays [SUB-9].
- Effort is already `medium` on the 5.5 models, and thinking can't be turned off. Use `low` for mechanical work and `high` only for hard reasoning [MOD-1, MOD-2].
- On Anthropic API auth, `/advisor opus` with a Sonnet main is the native "strong model judges" (experimental, API only) [SUB-7].
- The full pipeline: `/hetz:interview` → spec → `/clear` → `/hetz-crew:delegate <spec>` → judge → commit → `/hetz:handoff` → `/clear`.

## 5. Background sinks

- `/loop` and scheduled tasks send your full context every time they fire, even when you're idle. Use long intervals and stop them when done [BG-1].
- Goal check-ins and cross-session messages do the same. Use `CLAUDE_CODE_GOAL_CHECKIN_MINUTES=0` and `crossSessionInbound: "hold"` [BG-1].
- Prompt suggestions add a request per turn; `/hetz:setup global` turns them off [BG-2].
- Agent teams cost ~7x. Worktrees and parallel sessions multiply tokens; they buy speed, not savings [SUB-5].
- Subagent caches expire after 5 minutes. Give a subagent its whole job in one prompt rather than drip-feeding it.

## 6. Prompting

- Name the file, the symbol, the constraint and the check: "add validation to `login()` in `src/auth.ts`; `pytest tests/test_auth.py -q` must pass." Vague asks trigger broad scans [MISC-2].
- Ambiguous or multi-file work: run `/hetz:interview` (or plan mode, Shift+Tab) first. Questions asked at 5k context are the cheapest tokens you'll spend.
- Paste the decisive error lines, not the whole log, or let `runner` run it. Ask for edits, not whole-file rewrites.
- Noisy commands: use the quiet flags in [quiet-flags.md](quiet-flags.md); `cmd > log 2>&1; echo exit=$?; tail -40 log`; `set -o pipefail` before any pipe [OUT-5].
- Prefer CLIs (`gh`, `aws`) over MCP servers; disable unused servers with `/mcp`; check `/context` [MISC-6].
- In typed languages, an LSP/code-intelligence plugin replaces grep-and-read loops.
- For scripted runs: `claude -p … --max-budget-usd 1 --max-turns N`. `--bare` skips hooks, plugins and CLAUDE.md, but it needs API-key auth, not a subscription [MISC-3].

## 7. Monthly

`/hetz:slim` after big CLAUDE.md edits · `claude plugin details <plugin>` for listing cost · `/usage` and `scripts/hetz_ledger.py --since 30d` for drift · after a Claude Code upgrade, re-run the checks in [EVIDENCE.md](EVIDENCE.md#re-verify-after-an-upgrade).
