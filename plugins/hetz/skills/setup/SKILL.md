---
name: setup
description: Configure hetz for this machine (global) or this repo, with preview and undo.
disable-model-invocation: true
argument-hint: "[global|repo|status|undo [global|repo]]"
allowed-tools: Bash(python3 *hetz_settings.py*), AskUserQuestion, Read
---
# /hetz:setup

Never edit settings JSON yourself. Every change goes through
`S` = `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/hetz_settings.py" --project "${CLAUDE_PROJECT_DIR}"`.
Show S output verbatim in a code block; do not re-explain it.

Current state:
!`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/hetz_settings.py" --project "${CLAUDE_PROJECT_DIR}" status`

Mode = first word of "$ARGUMENTS": empty or `global` → GLOBAL; `repo` → REPO; `status` → STATUS; `undo` → UNDO.

## GLOBAL
1. One AskUserQuestion built from the `ask:` lines: one multiSelect question per line, header = the line's name. Each option: label = item id, plus " (Recommended)" when marked `*`; description = its text and any `[state]`. Add the `!` note that concerns an item to that item's description.
   - `launchers`: mention the login shell from the state and any "shadows" note.
   - A line with one item → options: that id / Not now. Nothing to ask → say all is applied; stop.
2. Run `S apply --scope user --items <chosen ids, comma-separated> --dry-run`.
3. One AskUserQuestion: "Apply these changes?" Apply (Recommended) / Cancel. If the dry run has `!` lines, add "Replace your existing values?": multiSelect over their keys (2-4 options, description = current → hetz value); for a single key use Keep mine (Recommended) / Use hetz value.
4. Apply: `S apply --scope user --items <ids>` plus `--force-keys <chosen keys>` if any.
5. Relay conflict advice from the state notes in one line each:
   - caveman enabled → suggest `claude plugin disable <that plugin>`.
   - rtk hook → never run rtk and squeeze on the same command; set rtk `exclude_commands` for git diff, cat, ls.

## REPO
1. Run `S deny-scan`.
2. One AskUserQuestion: multiSelect question(s) of candidate rules (max 4 options each; `[x]` → Recommended) and a question "Where?": `.claude/settings.local.json` personal (Recommended) / `.claude/settings.json` team.
3. Run `S apply --scope local --deny '<rule>' --deny '<rule>' ... --dry-run` (`--scope project` for team), confirm once (Apply / Cancel), then the same command without `--dry-run`.
4. End: "Deny rules apply in new sessions. Self-test: ask Claude to Read one denied file. For CLAUDE.md trimming run /hetz:slim."

## STATUS
Summarize the state above in at most 8 lines: what is on, what is offered, the notes. No tool calls.

## UNDO
`undo` or `undo global` → scope `user`; `undo repo` → scope `local`, then `project`.
Per scope: `S undo --scope <s> --dry-run`; if it changes anything, confirm once, then `S undo --scope <s>`.
