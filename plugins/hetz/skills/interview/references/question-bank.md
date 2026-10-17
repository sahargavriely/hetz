# Interview question bank

Ask only what changes the implementation. A fact the repo can answer goes to Explore. A low-impact choice gets a default and is listed in the spec.

## Rework ranking
Rank each open decision by what a wrong guess would cost to undo.
- HIGH, always ask: a public interface or data shape other code or users depend on; persisted data, schemas, migrations; auth, permissions, secrets, money, deletion or anything irreversible; scope boundaries; a choice that decides which modules get touched.
- MEDIUM, fill spare slots in a call; after "Keep probing" at a checkpoint, ask the rest; otherwise default: error behaviour, limits and performance targets, compatibility window, config surface, test strategy, rollout.
- LOW, never ask: naming, internal structure, log wording, code style (follow repo conventions), step order.
- Default rule: existing repo convention > smallest scope > reversible > standard and boring.

## Nodes, parents first
Explore settles the bracketed facts before anyone asks.

1. Goal and outcome [related code, issue text, prior commits]
   - What observable change means done? Offer the 2–3 plausible outcomes.
   - Fix, feature or refactor? This decides whether a failing test comes first.
   - Who hits it: end user, API client, operator, developer?
2. Scope and non-goals [callers, sibling features]
   - Minimal (one path) vs complete (all callers) vs generalized.
   - Name the tempting adjacent work and confirm it is out.
3. Interface [existing CLI/API/UI patterns, naming conventions]
   - Exact shape: flags, endpoint plus payload, function signature, UI entry point. Use the `preview` field to compare 2–3 shapes side by side.
   - Sync vs async; one call vs batch; defaults when inputs are omitted.
4. Data [schemas, migrations dir, fixtures]
   - New field or table vs derived at read time; backfill needed?
   - Migration forward-only vs reversible; what happens to existing rows?
5. Edge cases and errors [current error style, retry helpers]
   - Fail loudly, degrade, or retry? Per case: empty input, duplicates, partial failure, concurrency, timeouts.
   - What the user sees on failure: message, exit code, HTTP status.
6. Performance and security [hot paths, auth middleware, rate limits]
   - Expected size or rate; any hard limit?
   - Who may call it; what input is untrusted; does anything sensitive get logged?
7. Compatibility [supported versions, public API surface, feature flags]
   - Break, deprecate or keep the old behaviour? Behind a flag?
8. Acceptance [test runner, CI jobs, existing test style]
   - Which runnable checks prove it: unit, integration, manual step?
   - Is a test for the reported bug required before the fix?
9. Rollout [release process, changelog, docs]
   - Docs or changelog updates? Feature flag default? Anything to announce?

## Round mechanics
- Before round 1, show the facts you will rely on (≤5 lines, path:line) so a wrong lookup gets corrected now.
- Options are concrete outcomes ("Return 404 and log once"), never labels ("Option A").
- Each option's description states its consequence or cost ("adds a migration", "touches 6 files").
- One question per decision per round. Fold yes/no trivia into the recommended default.
- multiSelect for non-exclusive sets (platforms, formats, callers to update).
- A typed "Other" answer overrides the recommendation; restate it as a decision.
- When an answer contradicts an earlier answer or a repo fact, the next round opens with that conflict.
- Before each round print one status line: `Settled N · open: <high-rework decisions>`.
- Checkpoint question: "Keep probing (Recommended while high-rework decisions are open)" vs "Accept your defaults for the rest".
- Last-round question: "Anything I haven't asked that would change the design?" Options: "No, write the spec (Recommended)", "Yes" (the user types it in Other).

## Spec format (≤80 lines)
```
# <title>
Goal: <1–2 lines: observable outcome>
Non-goals: <bullets>

## Decisions
- <question> → <answer> (user|default)

## Interfaces
<signatures, flags, endpoints, schemas; no bodies>

## Acceptance checks
- `<runnable command>` → <expected result>

## Files likely touched
- path:line — why

## Risks / unknowns
- <open item, and who or what resolves it>

## Plan
1. <step> [inline|Explore|runner|worker|judge] — files — check
```
Plan tags, as /hetz-crew:delegate routes them:
- inline: ≤3 tool calls, or needs judgment or this conversation.
- Explore: needs more than 3 reads or searches.
- runner: a command that prints more than ~2k chars (tests, builds).
- worker: a bounded edit to ≤5 named files with a check.
- judge: after the last worker step, or any step touching security, data or migrations.

Before writing, check that every HIGH decision is answered or listed under Risks / unknowns.
