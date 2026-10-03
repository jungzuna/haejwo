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
- Frontmatter key order: `name`, `description`, `model?`, `effort?`, `tools?` —
  `model` is optional; omit it to inherit the session model.
- `description` is the routing signal: role + when to use, one sentence.
- Body shape: one role paragraph → imperative behavior bullets → a final **`Report back:`** contract with a proportionality rule (no fixed cap; lead with what changed and the verification evidence), ending with `Judgment calls:` (behavioral choices the brief didn't settle, or `none`).

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
Shapes are for the reader; P11 governs — use them when they fit, never as ritual.
- Table criterion (discretionary): a table when the reader would SCAN comparable fields; never for narrative.
- Batch plan (once, at start of long/multi-phase work): `phase | what | worker | expected`.
- Milestone (per worker completion, when it fits): one line — `✓ <phase> — verified via <diff/tests> — commit <sha> — next: <phase>`.
- Long-run checkpoint (past the stated ETA, when it fits): elapsed + active phase + "no result yet" + when the next update comes. Honest silence beats fake progress; never a timer loop.
- Final scorecard (multi-phase/commit-bearing work only): shipped (user terms) / quality gates / commits / deviations from plan / pending decisions. Small work: two plain lines instead.
- Scale ceremony by scope; numeric thresholds are internal heuristics, never visible rules.

## Reasoning policy
- Effort policy: see the injected rules (`rules/orchestration.md`) — the single source. Mechanics only here: `CODEX_EFFORT` overrides the stored `codex.effort`, and non-reasoning probes (connectivity smokes) stay explicit `low`.

## Maintenance
- Any prompt change bumps `plugin.json` version — patch for wording, minor for behavior.
- Non-obvious guards carry their origin inline (incident + why), plus the ceiling/removal condition where applicable — scars belong next to code.
- New prompt surface → add it to the Scope list above and follow the matching skeleton.
- Before commit: `python3 tests/test_hooks.py` must pass — gate the commit on the UNPIPED exit code, never on eyeballing tailed output; a pipe hides the failure exactly when it matters.
- Editing any `commands/*.md` requires regenerating `codex-skills/` mirrors (drift is canary-tested).
