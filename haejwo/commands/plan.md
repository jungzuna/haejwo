---
description: Run pre-implementation consensus with an independent reviewer (read-only), then carry the agreed plan into delegate briefs. The host invokes this proactively; users never need to type it.
argument-hint: "<topic to plan> [--reviewer codex|claude]"
---

You are the **haejwo host**. Drive pre-implementation consensus — planning outweighs implementation, and different models see different failure modes, so the plan gets debated BEFORE any code. The user's input: **$ARGUMENTS**

## 0. Scope the rigor (don't over-ceremonize)
Infer the scope: architecture / feature / refactor / bugfix / investigation. Scale depth accordingly. If the work is trivially small (typo-tier, single obvious change), SAY SO and offer to skip planning — this command is for decisions worth debating.

## 1. Draft
Write your plan draft: goal, key decisions + rationale, alternatives you considered, risks/unknowns, implementation checklist. Note your own uncertainties explicitly — the reviewer should attack the real tensions.

## 2. Reviewer critique (round 1)
Reviewer selection: `--reviewer` if given; else the configured other-CLI reviewer when enabled+verified (codex on Claude hosts, claude on Codex hosts); else fall back per Recovery rules. If fallback is used, say so in ONE plain sentence (weaker independence); no tier jargon beyond that. Name the reviewer in your updates.
- **codex** — self-contained brief (plan + context + your tensions; reviewer may not read the repo) to `${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh --mode consult <brief>` in the background; wait for completion once. Effort follows stakes: standard rounds use the runner default (`high`); escalate to `CODEX_EFFORT=xhigh` for architecture-level forks or a final deadlock round; `medium` suffices for routine sanity checks. Instruct: "rebut with evidence levels FACT/INFERENCE/SPECULATION; do not just agree."
- **claude** — self-contained brief to `${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh --mode consult <brief>` in the background; wait for completion once. Use the same rebuttal instruction.
- **fallback** — Claude host: spawn `haejwo:deep-reasoner`; Codex host: native same-model subagent. Use the same rebuttal instruction.

## 3. Disagreement ledger (the anti-fake-convergence core)
Convert every reviewer objection into a ledger row: `# | objection | evidence level | host response | status`. Status must be one of **accepted** (plan changed — say how), **rejected** (with grounded rationale), **deferred** (explicitly parked, with why). Capitulation without rationale is not a valid status. The ledger is YOUR debate discipline — surface only the material disagreements and their resolutions to the user, not the ceremonial full table.
- Rounds 2-3 (only if substantive objections remain): send the REVISED plan + ledger back via `--resume` (codex remembers the session) or the same subagent. Round 2 = revised plan + remaining objections; round 3 = final objections or deadlock. **Hard cap: 3 rounds.** Do not re-litigate settled rows unless new evidence changes them.

## 4. Converge or escalate
- **Consensus** = every ledger row has a status + rationale, and no substantive objection stands unaddressed.
- **Deadlock** = grounded disagreement survives round 3 → do NOT fake-converge. Present both positions with their evidence to the USER (AskUserQuestion) as tiebreaker.

## 5. Consolidate (conversation-first)
After reviewer consensus, surface the compact **Consensus outcome** shape (see PROMPTS.md Reporting shapes: `Consensus: <decision> — accepted n / rejected m (key rejection + why) → carried into brief`) BEFORE implementation — it replaces prose, scannable at a glance. Then present the agreed plan IN CONVERSATION as a compact summary — decisions + rationale, material objections and how they resolved, risks/open unknowns, and the implementation checklist chunked into **ready-to-send delegate briefs**. Write a `docs/plans/<YYYY-MM-DD>-<slug>.md` file ONLY if the user asks for a record (then include the full ledger, rejected alternatives, and a deviation log — do not sanitize; the debate is the value).

## 6. Linkage & drift control
- Every subsequent **feature-scale delegate brief must EMBED the agreed plan summary** (a `Plan:` section) or state `No plan because: <reason>`. Mirror work qualifies with `Plan: mirror <source> + preserve <material forks>` — a bare mirror name is not a plan. Workers are instructed to question feature-scale briefs missing the marker, or mirror plans that name a source but omit the material forks (what's preserved, what must not generalize from it).
- If implementation invalidates a plan assumption: say so plainly, adjust the plan before continuing (and update the plan file if one exists). A stale plan silently drifted-from is worse than no plan.
