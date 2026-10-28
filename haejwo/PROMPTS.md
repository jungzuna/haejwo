# haejwo prompt & style policy

Scope: **every LLM-facing text surface** — `commands/*.md`, `agents/*.md`, `rules/*.md`, hook-emitted messages (`gate.py`, `bash_guard.py`, `delegation_gate.py`, `report_check.py`, `post_bash.py`, `session_brief.py`, and the once-notes in `hjw_common.py`), the `status_collect.py` block the host reads, the `setup_probe.py` JSON strings the host relays (`why`, `cause`, `detail`), script comments/errors (`scripts/`), and README language that defines identity or behavior.

## Language
- **English everywhere by default** — prompts, comments, error messages, docs.
- Korean only as a proper noun or quoted term where the word itself IS the meaning (e.g. haejwo/해줘) — never for instructions. Localized user docs (`README.*.md`) are the one exception.

## Command files (`commands/*.md`)
- Frontmatter: `description:` one sentence, verb-first, states what it does and whether it is read-only or writes; `argument-hint:` always present; `model:` optional (Claude Code host only; the generated Codex mirror drops it).
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
- `session_brief.py` caps the injected context (rules + the one-line config summary, plus the setup nudge before setup) at 5,000 chars. Past the cap — or with the rules file unreadable — it never truncates: the whole rules text is replaced by a trusted emergency core, and the nudge and config summary still follow. Two limits, two roles: **3,750 bytes** is the diet ratchet `tests/test_hooks.py` pins on the rules file (the working budget; re-pinned 2.31.1 for the acceptance/outward record, 2.32.0 at a measured 3,744); the binding ceiling is the 5,000 total on the worst supported combination (Codex host with tier names rewritten, unconfigured with a stored budget, `HAEJWO_GATE=off`, an installed-cache root path), measured at 4,956 chars in 2.31.1 — 44 chars of headroom — and 4,857 in 2.32.0 (a 52-char cache root; 143 headroom; the combined fixture in `tests/test_hooks.py` re-measures it); measure every combination, never one path; the code checks only the 5,000 total. The rules own the record's two-line template; docs point to it, and the one hook that nudges for it (`report_check.py`) repeats the two labels verbatim (its placeholders omit the `| none` alternative, which cannot apply after a push) — change both when either label changes. 2.33.0 added per-event hook text without growing the rules (still 3,744 bytes). Per-event text (the runner-call and push-returned notes, the Stop reason's call ids) is OUTSIDE the 5,000 SessionStart ceiling: `tests/r3_a.py` (run by `tests/test_hooks.py`) pins each string's maximum rendered length (2-digit counts, 30-char ids), and README states the per-session exposure formula. The plugin README Contract is the full mechanism catalogue; the rules name only the subset the host acts on (the byte ratchet above).

## Hook-emitted text (the model reads these verbatim)
- Prefixes: `[haejwo gate]` for gate/bash-guard/delegation decisions; `[haejwo]` / `[haejwo config]` for session context and per-event notes; `[haejwo status]` for the status collector's block.
- A deny reason must contain, in order: what was blocked → why (budget/rule state) → the EXACT next action (delegate to whom) → the escape hatch.
- One sentence per fact; zero filler. **Changing these strings requires updating `tests/test_hooks.py` assertions** — the deny strings are a tested contract.
- Notes are never denials. The retained re-presentation layer is the PostToolUse notes (`post_bash.py`: the runner-call and push-returned notes); each pairs ONE existing obligation with event facts (runner basename, tool call ids) and adds no demand (P13). Quoted output carries a provenance label (`quoted shell output tail`) and is sanitized; wording separates detection from execution, completion and the remote outcome. Placeholders: the quoted tail is the last line non-empty after sanitizing (an ANSI-only last line is skipped). Byte-exact strings and the maximum rendered length of every per-event string including the Stop reason are a tested contract too. Delivery was measured on Claude Code 2.1.310 headless; roles per the CLI transcript; effect was not.

## Tone & emphasis
- Imperative, present tense. No marketing adjectives, no apologies.
- CAPS for absolute invariants (NEVER / MUST / ONLY), **bold** for key terms, `code` for identifiers, paths, and commands.

## Reporting shapes (host output during orchestration)
Shapes are for the reader; P11 governs — use them when they fit, never as ritual.
- Table criterion (discretionary): a table when the reader would SCAN comparable fields; never for narrative.
- Batch plan (once, at start of long/multi-phase work): `phase | what | worker | expected`.
- Milestone (per worker completion, when it fits): one line — `✓ <phase> — verified via <diff/tests> — commit <sha> — next: <phase>`.
- Long-run checkpoint (past the stated ETA, when it fits): elapsed + active phase + "no result yet" + when the next update comes. Honest silence beats fake progress; never a timer loop.
- Final scorecard (multi-phase/commit-bearing work only): shipped (user terms) / quality gates / commits / deviations from plan / pending decisions. Small work: two plain lines instead. An acceptance report, and any report after an outward action, ends with the rules' two record lines (`Final-diff review:` / `Outward actions:`) — never other replies (P11).
- Scale ceremony by scope; numeric thresholds are internal heuristics, never visible rules.

## Reasoning policy
- Effort policy: see the injected rules (`rules/orchestration.md`) — the single source. Mechanics only here: `CODEX_EFFORT` overrides the stored `codex.effort`, and non-reasoning probes (connectivity smokes) stay explicit `low`.

## Maintenance
- Any prompt change bumps `plugin.json` version — patch for wording, minor for behavior.
- Non-obvious guards carry their origin inline (incident + why), plus the ceiling/removal condition where applicable — scars belong next to code.
- New prompt surface → add it to the Scope list above and follow the matching skeleton.
- Before commit: `python3 tests/test_hooks.py` must pass — gate the commit on the UNPIPED exit code, never on eyeballing tailed output; a pipe hides the failure exactly when it matters.
- Editing any `commands/*.md` requires regenerating `codex-skills/` mirrors (drift is canary-tested).
