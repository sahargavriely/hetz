---
name: interview
description: Interview me until the task is fully specified, then write a spec for a fresh session.
disable-model-invocation: true
argument-hint: "<idea, issue # or URL>"
allowed-tools: Read, Grep, Glob, Agent(Explore), Bash(gh issue view *), Bash(python3 -I "${CLAUDE_SKILL_DIR}/prep.py")
---

Interview me about: $ARGUMENTS

Settle every decision that would change the implementation, then write a spec. Never implement, edit code or run builds here.

Repo facts:
!`python3 -I "${CLAUDE_SKILL_DIR}/prep.py"`

1. TRIAGE. No request above → ask for it in one line. Issue #/URL → `gh issue view <n> --comments`. Goal, scope, interface and acceptance checks already explicit → say "Spec complete, no questions" and go to 5.
2. FACTS. Never ask what code, git history or docs can answer. Send 1–3 parallel Explore agents, one narrow question each (patterns to follow, files to touch as path:line, test setup). Look up new facts as answers raise them.
3. TREE. Read `${CLAUDE_SKILL_DIR}/references/question-bank.md`. List open decisions, rank by rework if guessed wrong, drop those the facts settle.
4. ROUNDS, via AskUserQuestion:
   - ≤4 questions per call, highest rework first, parents before dependents; derive follow-ups from answers.
   - Recommended option first with a one-line why; 2–3 real alternatives, each with its cost.
   - Never ask low-impact decisions; choose a default.
   - Continue while any high-rework decision is open. After round 3, and every 2 rounds after, add "Keep probing / Accept your defaults for the rest".
   - "enough" or "go" at any time → take the recommendation for everything open.
   - The last round asks: "Anything I haven't asked that would change the design?"
5. SPEC. Write a new `.claude/specs/<kebab-slug>.md` (specs dir above; none shown? add `.claude/specs/` to .git/info/exclude) in the question bank's Spec format, ≤80 lines, no code beyond signatures.
6. CLOSE: list the defaults you chose, one line each, so I can veto them. Then exactly:
   Spec: <path>. Fresh start: /clear (pick the model now), then `/hetz-crew:delegate <path>` — or `implement <path>` for small specs.
   Offer to continue in this session only if the spec is ≤15 lines.
