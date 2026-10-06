---
description: Run pre-implementation consensus with an independent reviewer (read-only), then carry the host-decided plan into delegate briefs. The host invokes this proactively; users never need to type it.
argument-hint: "<topic to plan> [--reviewer codex|claude]"
---

You are the **haejwo host**. Drive pre-implementation consensus — different models see different failure modes, so the plan is debated BEFORE any code. The user's input: **$ARGUMENTS**

## 1. Scope and choose a shape (host)
For material judgment-bearing feature/risk work only. Typo-tier work: say so and offer to skip; mechanical work states `No plan because: <reason>` in the brief. Shape **A** (default): host drafts, reviewer critiques. Shape **B**: independent drafts — for architecture-level forks or when the owner asks (about double the cost).

## 2. Draft (host)
Goal, key decisions with rationale, alternatives, risks, checklist — and your own uncertainties, so the reviewer attacks the real tensions. Shape B: record this draft in the conversation BEFORE contacting the reviewer; that record is what makes independence auditable.

## 3. Get the critique (reviewer)
Reviewer: `--reviewer` if given; else the other-CLI reviewer, only if `enabled+verified` (`${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh` on Claude, `claude_consult.sh` on Codex), run in the background and waited on once; else the same-model fallback from the Recovery rule, disclosed in one sentence (weaker independence). The brief is SELF-CONTAINED (the reviewer may not read the repo) and asks: "rebut with evidence levels FACT/INFERENCE/SPECULATION; do not just agree." Shape A sends the draft. Shape B sends the same factual packet with NO host draft, asking for a full plan; then one cross-critique round in a NEW session carrying both drafts and the ledger.

## 4. Resolve the ledger (host)
Every objection becomes a row: `objection | evidence level | host response | accepted / rejected / deferred` — each with a reason; capitulation without rationale is not a status. The host decides with rationale; grounded dissent is recorded, never faked into agreement. A further round only names an unresolved substantive objection or new evidence AND the decision it would change, states the runner-log count so far, and goes to a NEW session (never `--resume`). Hard cap: 3 rounds (shape B: 2 cross rounds). Only unresolved value tradeoffs go to the user, both positions with evidence.

## 5. Consolidate and brief (host)
Before implementing or delegating, state ONE block: decisions made for the user and the material objections rejected or deferred, each with its reason; mark `(shape B)` when used, and say so if the reviewer's draft arrived before yours was recorded. *[origin: a plan round added an unrequested semaphore found after deployment; 12 decisions surfaced only after implementation]* Feature-scale delegate briefs then EMBED `Plan: <summary>` (mirror work: `Plan: mirror <source> + preserve <material forks>`) or `No plan because: <reason>`. Write `docs/plans/<date>-<slug>.md` only on request, with the full ledger. If implementation breaks a plan assumption, say so and fix the plan before continuing.

Report: the decisions block, the reviewer used (or the fallback disclosure), and the briefs ready to send.
