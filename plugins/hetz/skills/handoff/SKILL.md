---
name: handoff
description: Save session state to a ≤40-line note so you can /clear and continue cheaply.
disable-model-invocation: true
argument-hint: "[focus]"
allowed-tools: Bash(python3 -I "${CLAUDE_SKILL_DIR}/state.py")
---

Write a handoff note so this session can be cleared and continued cheaply. Focus: $ARGUMENTS

Repo state (gathered; run git only where it says UNKNOWN):
!`python3 -I "${CLAUDE_SKILL_DIR}/state.py"`

No `note:` line above? Use `.claude/handoff.md`, add it to .git/info/exclude, and run `git status -s` and `git log --oneline -5`.

1. In one Write, overwrite the `note:` path above. ≤40 lines, terse, only from this conversation and the state above. With a focus, keep only what serves it.
   ```
   # <goal, one line>
   Done: <what, with commit SHAs>
   In progress: <exact state; uncommitted files and why>
   Decisions: <choice — one-line why>
   Key locations: <path:line — what> (≤10)
   Verify: <exact commands, quiet flags>
   Next: <≤3 ordered steps>
   Dead ends: <tried, failed, why; don't retry>
   Spec: <path, if any>
   ```
2. Reference paths. Never paste code over 5 lines, logs, env values or secrets.
3. Reply with the note path and one next step for the user to type (skills cannot run these):
   - new or unrelated task → `/rename <slug>`, then `/clear`. Free; a new session in this directory is pointed at the note.
   - same task, recent activity, context under ~150k → `/compact <one-line focus>` also works.
   - abandoning the current approach → `/rewind` instead.
