---
name: slim
description: Audit this repo's always-loaded context and noisy files; propose trims as one diff.
disable-model-invocation: true
argument-hint: "[--deny-only] [--plugins]"
allowed-tools: Bash(python3 -I "${CLAUDE_SKILL_DIR}/slim_audit.py" *), Read, Edit(**/CLAUDE.md), Edit(**/CLAUDE.local.md), Edit(.claude/rules/**), Edit(.claude/skills/**), Edit(~/.claude/CLAUDE.md), Edit(~/.claude/projects/**/memory/**), AskUserQuestion
---

!`python3 -I "${CLAUDE_SKILL_DIR}/slim_audit.py"`

Script = `python3 -I "${CLAUDE_SKILL_DIR}/slim_audit.py"`. ARGUMENTS --deny-only → steps 5-7 only; --plugins → also Script --plugins-only.

1. Summarize in ≤10 lines, biggest standing cost first.
2. Read each always-loaded CLAUDE.md, CLAUDE.local.md, import, rule; classify every section:
   - KEEP: non-inferable commands, hard always/never rules, non-obvious conventions, output-length rules; rewrite as imperative one-liners.
   - SCOPE: area-specific → `.claude/rules/<area>.md` with `paths:` globs.
   - SKILL: release/deploy/migration procedure → `.claude/skills/<name>/SKILL.md` with `disable-model-invocation: true`.
   - COMMENT: human-only notes → `<!-- -->` block (stripped, free).
   - DOCS: reference → a docs/ file named by path, never @import (loads in full).
   - DELETE: derivable from code, generic advice, duplicates of the output style.
   Target: root ≤60 lines (~800 tok), never >200.
3. COMPACT missing → append the audit's template lines to the root CLAUDE.md.
4. Auto memory (path above): MEMORY.md → dedupe, drop stale, ≤50 lines; INDEX → name stale topic files to delete.
5. Deny: one choice per group, rules verbatim; [x] pre-selected, [ ] opt-in. Migrate .claudeignore rules; offer deleting it. Never deny source, config or files the user edits.
6. Show one unified diff of all edits + rules; ask once with AskUserQuestion: all / pick / none. Apply:
   - First Script `--backup <files>` (uncommitted → .hetz-bak). Team-owned CLAUDE.md → offer a branch + PR instead.
   - Rules: Script `--add-deny --scope local '<rule>' ...` (`--scope project` = shared .claude/settings.json; ask). Never hand-edit settings JSON.
7. End: "CLAUDE.md changes apply after /clear; deny rules in new sessions. Self-test: ask Claude to Read one denied file."

No automatic whole-file compression; edit section by section.
