# Trying RTK safely (optional)

hetz does not install RTK; [DECISIONS.md §2](DECISIONS.md#2-rtk-compress-command-output-with-rust-token-killer) explains why. If you want to try it anyway, this is the low-risk path. Checked against RTK 1.0.1 and Claude Code 2.1.272 on 2026-10-17. RTK moves fast, so re-check before installing.

## What you get, and what you don't

- RTK is a binary plus a **PreToolUse hook** that rewrites Bash commands before they run, e.g. `git status` → `rtk git status`. The hook only sees Bash. Read, Grep and Glob bypass it [RTK-1].
- Its "60–90%" is shell-output bytes, not your bill [RTK-2]. The largest measured Bash outputs were file dumps, and RTK summarises `cat`, which is a fidelity risk [COST-9].
- Its strengths are `git status/log/push`, `gh`, `docker ps`, `kubectl get` and test runners. hetz's squeeze deliberately leaves the first four alone.

## 1. Install the Apache-only edition

```sh
brew install rtk-ai/tap/rtk-oss     # Apache-2.0 only; no BUSL engine, proxy or PostToolUse pipeline
rtk --version && rtk gain           # `rtk gain` must show a savings dashboard
```

- `rtk-oss` leaves out the BUSL-1.1 engine, `rtk pipeline`, PostToolUse compression and delegation [RTK-5]. The PostToolUse part would collide with squeeze, so this is the edition to use.
- Avoid `curl … | sh` (supply-chain risk) and plain `cargo install rtk`: the crates.io name belongs to an unrelated "Rust Type Kit" [RTK-7]. If `rtk gain` says "not a rtk command", you have the wrong binary.
- Do **not** start the RTK proxy (`rtk claude`, or the proxy offer in a full-edition `rtk init -g`). Any `ANTHROPIC_BASE_URL` proxy makes Claude Code load every MCP tool schema upfront unless `ENABLE_TOOL_SEARCH=true` [MISC-4].

## 2. Hook only, nothing in CLAUDE.md

```sh
cp ~/.claude/settings.json ~/.claude/settings.json.pre-rtk   # your own backup
rtk init -g --hook-only     # PreToolUse hook only; does not touch ~/.claude/CLAUDE.md
rtk init --show             # confirm the hook is registered
```

- Plain `rtk init -g` also writes `~/.claude/RTK.md` and an `@RTK.md` import into `~/.claude/CLAUDE.md`. That is always-on text in every repo, which hetz avoids.
- The hook is written into `~/.claude/settings.json`. Afterwards run `/hetz:setup status`: it reports the RTK hook as a conflict to resolve. hetz's setup re-reads the file before every write, so the two don't clobber each other.
- Permission rules are checked against the **rewritten** command. Deny and ask rules still apply, but an allow rule for `git status` may need an `rtk git status` form too.

## 3. Exclusions: one owner per command

**Never let RTK and squeeze filter the same command.** By design, squeeze passes through any command that starts with `rtk`. If your Claude Code version shows PostToolUse hooks the original command instead of the rewritten one, both would act on it. The exclusions below prevent that too: RTK never rewrites the noisy tools squeeze owns, or the fidelity-risky commands.

Edit `~/Library/Application Support/rtk/config.toml` on macOS (`~/.config/rtk/config.toml` on Linux) [RTK-8]:

```toml
[hooks]
# Fidelity: keep full content (diff context, file bodies, hidden build dirs)
# Squeeze's allowlist: let hetz own install/build/test output
# Open exit-code bugs (#2974 pnpm/eslint, #4421 mvn/bun): never rewrite
exclude_commands = [
  "git diff", "git show", "git log -p", "git rebase", "git cherry-pick", "cat", "ls", "diff",
  "npm", "pnpm", "yarn", "bun", "pip", "uv", "poetry", "brew",
  "cargo", "go", "make", "gradle", "gradlew", "mvn", "mvnw",
  "pytest", "jest", "vitest", "tsc", "eslint", "terraform",
  "docker build", "docker compose", "docker pull",
]
```

- Patterns match the full command after env prefixes (`VAR=x`) are stripped. RTK also peels wrappers, so `"pytest"` covers `python3 -m pytest` and `"gradlew"` covers `./gradlew`. Subcommand patterns (`"git push"`) and `^regex` both work.
- Exclusions can't stop the line-range forms `head -20 f` and `tail -n 5 f`: those always rewrite.
- That leaves RTK owning git status/log/push, `gh`, `docker ps` and `kubectl`.
- **RTK-only alternative:** if you want RTK to own test runners instead, set `HETZ_SQUEEZE=0` and drop the second and third groups. Re-check the exit-code issues first.
- One-off escapes: `RTK_DISABLED=1 <cmd>` or `rtk bypass <cmd>`.

## 4. A/B it before keeping it

`rtk gain` counts bytes it removed, not billed tokens, so it cannot decide this [RTK-2]. Use your bill:

1. **Field A/B (free):** run `scripts/hetz_ledger.py --since 7d --json > before.json` on a normal week without RTK. Install RTK and work a comparable week, then run `scripts/hetz_ledger.py --compare <start1>..<end1> <start2>..<end2>`. Compare $/active day, Bash chars per call, and cache-write share.
2. **Bench A/B (a few dollars):** arm C (hetz hooks) vs arm C + RTK on the `noisy-suite` (noisy build plus a failing suite) and `failing-test` bench tasks. Use k=5, pinned model, effort and version, and `--max-budget-usd` on every run.
3. **Keep RTK only if** median cost drops ≥3% with no lost passes. Also watch for phantom-failure loops, where Claude retries something that actually passed: that is the #3492 pattern [RTK-4].

## 5. Known issues (open on 2026-10-17 unless noted)

- **Failures shown as success or exit code masked:** #4421 (rspec, phpunit, mvn, bun, deno, ctest, sbt), #3267 (`diff`), #2974 (pnpm/eslint) [RTK-3].
- **Fidelity:**
  - #3434: `rtk ls` silently hides build, dist, target, coverage, venv and node_modules.
  - #3411: the PHP filter drops the expected side of an assertion diff.
  - #4364: the first command of a pipe gets rewritten.
  - By design: `git diff` loses context lines and `cat` returns a structure summary, while RTK's awareness text tells the model to treat output as complete.
- **Cost:** #3492 measured +16% cost per passing run (disputed; not reproduced on v0.48.0) [RTK-4].
- **Privacy:**
  - The recall store keeps full output of failed commands on disk, which may include secrets (#1875). Set `RTK_RECALL=0` per command, or `[retriever] mode = "disabled"`.
  - The tracking DB stores full command lines (#1160).
  - Telemetry is opt-in; leave it off.
- **Maturity:** 1.0.0 shipped 2026-10-13 and 1.0.1 on 2026-10-14. Before that, minor releases came every 1–2 weeks [RTK-6].

## 6. Uninstall

```sh
rtk init -g --uninstall
brew uninstall rtk-oss
```

Restart Claude Code, then run `/hetz:setup status` to confirm the hook is gone. Restore `~/.claude/settings.json.pre-rtk` only if something else looks wrong; it predates any later changes.
