# haejwo prompt & style policy

Scope: **every LLM-facing text surface** — `commands/*.md`, `agents/*.md`, `rules/*.md`, hook-emitted messages (`gate.py`, `bash_guard.py`, `session_brief.py`), script comments/errors (`scripts/`), and README language that defines identity or behavior.

## Language
- **English everywhere by default** — prompts, comments, error messages, docs.
- Korean only as a proper noun or quoted term where the word itself IS the meaning (e.g. haejwo/해줘) — never for instructions. Localized user docs (`README.*.md`) are the one exception.

## Command files (`commands/*.md`)
- Frontmatter: `description:` one sentence, verb-first, states what it does and whether it is read-only or writes; `argument-hint:` always present.
- Body opens with the **role line**, always this formula:
  `You are the **haejwo host**. <one-line mission>. The user's input: **$ARGUMENTS**` (drop the input echo only when the command takes no arguments).
- Multi-phase commands: numbered `## N. <Verb phrase>` steps in execution order. Single-purpose commands (status/gate): one short list — no step ceremony.
- Every step names WHO acts (host / worker / user) and what unblocks the next step.
- End with the completion contract: what to report, write, or confirm.

## Agent files (`agents/*.md`)
- Frontmatter key order: `name`, `description`, `model`, `effort?`, `tools?`.
- `description` is the routing signal: role + when to use, one sentence.
- Body shape: one role paragraph → imperative behavior bullets → a final **`Report back:`** contract with a length cap, ending with `Judgment calls:` (behavioral choices the brief didn't settle, or `none`).

## Rules (`rules/orchestration.md`)
- Bold section labels; compact labeled paragraphs or bullets. Every rule actionable.
- Total injected size (rules + config summary) must stay under session_brief's 5000-char hard cap; keep ≤4600 so there's headroom for config lines.

## Hook-emitted text (the model reads these verbatim)
- Prefixes: `[haejwo gate]` for gate/bash-guard decisions; `[haejwo]` / `[haejwo config]` for session context.
- A deny reason must contain, in order: what was blocked → why (budget/rule state) → the EXACT next action (delegate to whom) → the escape hatch.
- One sentence per fact; zero filler. **Changing these strings requires updating `tests/test_hooks.py` assertions** — the deny strings are a tested contract.

## Tone & emphasis
- Imperative, present tense. No marketing adjectives, no apologies.
- CAPS for absolute invariants (NEVER / MUST / ONLY), **bold** for key terms, `code` for identifiers, paths, and commands.

## Reporting shapes (host output during orchestration)
- Table criterion: use a table whenever the reader would SCAN comparable fields (even 2 rows); never for narrative.
- Batch plan (once, at start of long/multi-phase work): `phase | what | worker | expected`.
- Milestone (per worker completion): one line — `✓ <phase> — verified via <diff/tests> — commit <sha> — next: <phase>`.
- Long-run checkpoint (once, past the stated ETA): elapsed + active phase + "no result yet" + when the next update comes. Honest silence beats fake progress; never a timer loop.
- Consensus outcome (after a reviewer round ONLY): `Consensus: <decision one-liner> — accepted n / rejected m (key rejection: X, because Y) → carried into brief: <what>`. Use a 2-4 row table (`decision | rationale | dissent-resolution`) only when ≥2 material decisions. This REPLACES the prose summary; never emit it when no reviewer round ran.
- Final scorecard (multi-phase/commit-bearing work only): shipped (user terms) / quality gates / commits / deviations from plan / pending decisions. Small work: two plain lines instead.
- Scale ceremony by scope; numeric thresholds are internal heuristics, never visible rules.

## Reasoning policy
- Reviewer effort scales with the decision's stakes: `medium` = routine checks, `high` = standard consults (runner default), `xhigh` = architecture forks / security-critical / final deadlock rounds only. Never pin one level for everything — uniform max dilutes budget where judgment compounds. Non-reasoning probes (connectivity smokes) stay explicit `low`. Claude-host same-family fallback = `deep-reasoner`; other-CLI review uses the configured runner.

## Maintenance
- Any prompt change bumps `plugin.json` version — patch for wording, minor for behavior.
- Non-obvious guards carry their origin inline (incident + why), plus the ceiling/removal condition where applicable — scars belong next to code.
- New prompt surface → add it to the Scope list above and follow the matching skeleton.
- Before commit: `python3 tests/test_hooks.py` must pass — gate the commit on the UNPIPED exit code, never on eyeballing tailed output; a pipe hides the failure exactly when it matters.
- Editing any `commands/*.md` requires regenerating `codex-skills/` mirrors (drift is canary-tested).
