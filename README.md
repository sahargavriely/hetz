# hetz

A token-saving kit for Claude Code (2.1.272+). It installs once at user scope and works in every repo. It goes after where the bill actually comes from: context size × number of requests, cache rebuilds, and bulky tool output. Output prose is a small part of it.

It is one marketplace (`hetz`) with two plugins:

- **hetz**: hooks plus user-invoked skills. It adds **0** always-on listing tokens.
- **hetz-crew**: model-matched subagents plus `/hetz-crew:delegate`. Its descriptions cost about 70 tokens on every request, and you can turn it off separately.

## Components

| Component | Kind | Invocation | Always-on cost | What it saves |
|---|---|---|---|---|
| squeeze | PostToolUse hook | automatic, on Bash | 0 | Replaces the *successful* output of allowlisted noisy tools (build, test, install, lint) with the error/warning lines, their context and a path to the raw log. Fixtures shrink 85–88%. Never touches `git`, `cat`, `grep` or any other content command. |
| session guard | SessionStart, UserPromptSubmit, Stop, PreModelSwitch hooks | automatic | 0 (its messages go to you, not the model) | Quiet tool env for Bash (`NO_COLOR`, `PAGER=cat`, no progress bars). `/clear` nudges at 100k/150k context and after a commit. Cost warnings on a cold resume and on a mid-session `/model` switch. One-time pickup of a fresh handoff note. |
| return contract | SubagentStart hook | automatic, built-in Explore / general-purpose | ~50 tokens per spawn, inside the subagent | Terse subagent results, which the parent re-reads on every later request. |
| `/hetz:setup` | skill | `global`, `repo`, `status`, `undo` | 0 (~630 when run) | Applies a settings profile after a preview, with a backup and a manifest: 200k compaction cap, 12k Bash/MCP output caps, Glob respects .gitignore plus min/map deny, Concise style, statusline. Optional: Explore on Haiku, shell launchers (`cc`, `cc-spec`, `cc-do`…), Sonnet default model, Read cap. `repo` proposes per-repo deny rules. |
| `/hetz:interview` | skill | `/hetz:interview <idea or issue>` | 0 (~470) | Settles ambiguity while context is tiny, then writes `.claude/specs/<slug>.md` for a fresh session. |
| `/hetz:handoff` | skill | `/hetz:handoff [focus]` | 0 (~320) | Writes `.claude/handoff.md` so you can `/clear` and continue; the next session picks it up automatically. |
| `/hetz:slim` | skill | `/hetz:slim [--deny-only] [--plugins]` | 0 (~470) | Audits always-loaded text (CLAUDE.md, rules, memory, plugins) and proposes anchored Read-deny rules for generated or bulky files. |
| statusline | script | installed by setup | 0 | Shows `model·effort │ ctx 142k/200k │ cache 96% warm │ $3.12` so rotation decisions are visible. |
| runner / worker / judge | hetz-crew agents | by `/delegate` or on request | ~57–72 total | Haiku runs noisy test/build commands and returns a verdict; Sonnet does bounded edits; Opus reviews the diff once per change. |
| `/hetz-crew:delegate` | skill | `/hetz-crew:delegate <task or spec>` | 0 (~590) | Routes a task by expected output size and difficulty instead of doing everything on the main model. |
| ledger, bench | dev scripts | terminal | 0 | Measure the above (see below). |

"Always-on" means tokens added to every request in every repo. Every hetz skill has `disable-model-invocation: true`, so 2.1.272 leaves it out of the model's skill listing. `claude plugin details hetz` still prints about 93 tokens because it counts user-only skills as well; `scripts/check.sh` measures the real model-facing figure.

## Install

```sh
~/Projects/hetz/scripts/install.sh        # adds the marketplace, installs hetz + hetz-crew at user scope
```

Then, in any Claude Code session, run `/hetz:setup global` once per machine. Optionally, run `/hetz:setup repo` or `/hetz:slim` once per repo; repo rules go to `.claude/settings.local.json` unless you pick the team file. The marketplace is a directory source, so edits to this repo apply after `/reload-plugins`.

## Usage

Settings take effect in **new** sessions only.

### First time in a repo

1. Open a new session. The status bar should read `sonnet │ ctx …/200k │ cache …% warm │ $…`.
2. Run `/hetz:slim` once. It audits CLAUDE.md and the other always-loaded text, and proposes deny rules for generated or bulky files.

### Day to day

| Situation | Do |
|---|---|
| Small, clear task | Just ask. Don't delegate: a subagent cold start costs 25–46k tokens. |
| Fuzzy or bigger task | `/hetz:interview <idea>` → it writes `.claude/specs/<slug>.md` → `/clear` → `/hetz-crew:delegate .claude/specs/<slug>.md` (or `implement <path>` for small specs). |
| Clear multi-step task | `/hetz-crew:delegate <task in plain words, plus how to check it>`. It shows a plan table and waits for your approval. |
| Switching to an unrelated task | `/hetz:handoff` → `/clear`. The next session picks up the note automatically. |
| Hard problem | Start a new session on a stronger model (`cc-deep`). Pick the model when you start; never `/model` mid-session, because that rebuilds the cache. |
| One command needs its full output | Append `# hz:raw` to that command. |

### Writing a spec for delegate

`/hetz:interview` writes one for you. To write one by hand, cover four things:

- **Goal:** one line.
- **Scope:** files to touch, and what not to touch.
- **Behavior:** signatures, inputs and outputs, edge cases.
- **Acceptance:** the command that proves it works.

### Reading the status bar

| Segment | Meaning | Act when |
|---|---|---|
| `ctx 142k/200k` | Context re-sent on every request | Yellow or red: `/hetz:handoff` → `/clear` |
| `cache 93% warm` | Share of context served from cache, at ~10% of the input price | 85–99% is normal. It is low on the first turn by design. |
| `cache COLD` | Cache expired (idle or model switch); the next turn re-pays the whole context | With a large context, hand off and clear instead of continuing |
| `$3.12` | Session cost so far | — |

### Every ~2 weeks

Run `python3 scripts/hetz_ledger.py --since 14d` and `--replay-squeeze --since 14d` from this repo to check that the kit pays off.

## Uninstall

`/hetz:setup undo global` (or `undo repo`), then remove the plugins. `scripts/uninstall.sh` does all of it without Claude: it undoes every change in `~/.claude/hetz/manifest.json`, uninstalls both plugins and removes the marketplace. Flags: `--dry-run`, `--keep-settings`, `--purge`.

## Switches

| Switch | Effect |
|---|---|
| `HETZ_SQUEEZE=0` | squeeze off (it is on by default). `# hz:raw` on one command skips it for that command. |
| `HETZ_GUARD=0` | no guard nudges or warnings (handoff pickup still runs) |
| `HETZ_CONTRACT=0` | no subagent return contract |
| `HETZ_QUIET_ENV=off` / `aggressive` | no quiet env / also `CI=1`, `PYTEST_ADDOPTS=-q -rfE` |
| `claude plugin disable hetz-crew@hetz` | drop the crew and its listing cost |

## Measure

- `python3 scripts/hetz_ledger.py --since 14d` reads your transcripts read-only and prints aggregates only: spend by token type, cost by context size, cache breaks with their causes, subagent cost, Bash output by command family, and squeeze's realized savings. Add `--json`, or `--compare 30d..14d 14d..`.
- `python3 scripts/hetz_ledger.py --replay-squeeze --since 30d` replays past Bash output through squeeze offline. It reports chars saved and KEEP lines lost, and exits 3 if any KEEP line would be lost.
- `bench/` is the controlled A/B harness (arms A–D plus leave-one-out ablations, k runs per cell). It costs real tokens, so start with `python3 bench/run.py --preset cheap -k 1 --warmup 0 --execute`. See `bench/README.md`.
- `scripts/check.sh` is the free gate to run before each commit: manifests validate, listing budget, every pytest suite, shell syntax.

## What happened to the original ideas

caveman became the built-in Concise style plus terse return contracts. RTK became squeeze, output caps and quiet env, with RTK documented as opt-in. `.claudeignore` (which Claude Code ignores) became Glob's .gitignore switch plus anchored deny rules. "Rotate sessions" became the 200k cap, guard nudges and handoff. Interview and delegation are kept, as a pipeline. The full reasoning is in [docs/DECISIONS.md](docs/DECISIONS.md), the evidence in [docs/EVIDENCE.md](docs/EVIDENCE.md), and the day-to-day habits in [docs/PLAYBOOK.md](docs/PLAYBOOK.md).
