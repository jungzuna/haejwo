# External skill repositories: what fits haejwo? (Claude × Codex, 2026-11-03)

**Owner's question:** "Analyze each of these one at a time. Is there anything worth taking that fits our plugin's philosophy? Analyze it carefully with GPT."

**Repositories:** all three are MIT-licensed. They were read as untrusted data from shallow clones, and nothing in them was executed.
- A: `cursor/plugins` → `pstack`, at `ae5650f`
- B: `mattpocock/skills`, at `4ae2317`
- C: `addyosmani/agent-skills`, at `301b02b`

**Method:** debate 10, two rounds.
- Three same-vendor read-only analysts each took one repository. They are not independent.
- In parallel, Codex (effort high, NEW session) analyzed all three without seeing the host's conclusions.
- Round 2 debated the four points where the two analyses disagreed. Codex signed P1 and P2, narrowed P3 and P4, and withdrew its round-1 "documentation examples now" recommendation.
- The host verified the key quotations against the clones.

## What each repository is (FACT, at the revisions above)
- **A, pstack.**
  - What it is: one author's working style, packaged as about 60 skills, 23 playbooks, 24 one-principle skills and 2 agents. It has no hooks, so it is norm-only.
  - How it works: the user activates it (`/poteto-mode`). Its README rejects planning, workers push and merge, and external actions such as team chat proceed without asking.
  - Review: a "diverse-model" panel, which on Claude Code silently falls back to the session model (`docs/harness/claude-code.md:35`).
  - Conflicts with: P1, plan-first, host-owned outward actions, and P9.
- **B, mattpocock/skills.**
  - What it is: small user-invoked or model-invoked skills, with no runtime enforcement. Review is two same-model sub-agents.
  - Scope policy: "an observed failure; a hypothetical improvement does not clear this part" (`SCOPE.md:9`), with rejected ideas recorded in `.out-of-scope/`. This is close to haejwo's K4 plus the BACKLOG reject option.
  - Conflicts with: P1 (setup and slash commands are user-managed) and P9.
- **C, addyosmani/agent-skills.**
  - What it is: 25 workflow skills, personas, commands and optional hooks. No hook enforces workflow discipline.
  - Its strongest material is its evaluation record (`evals/README.md:63-71`):
    - One description clause raised skill firing from 7/27 to 21/27, with 0/27 on prompts that must not fire it.
    - Firing of one skill dropped from 6/6 to 1/6 between CLI 2.1.283 and 2.1.312 with the wording unchanged.
    - In one case the skill fired every time, but the behaviour it adds appeared in 2/4 runs with the plugin against 0/3 without.
    - These are repository-reported figures; the results themselves are gitignored.
  - Conflicts with: Lean ("1% chance → must invoke"), P9 (same-model persona fan-out), the read-only reviewer contract (its reviewer edits code and runs tests), and P4 (a Read hook rewrites the user's source in place).

## Dispositions (signed after round 2)
| # | Candidate | Decision | Why (evidence level) |
|---|---|---|---|
| P1 | Reviewer-contract sentence from C: "Label defects that already exist in the base separately from regressions this change introduces. Zero findings is a valid result; do not invent one." | BACKLOG with reject, not now | It changes every consult's runtime prompt (K4). Saving rule bytes does not exempt it. Its benefit is SPECULATION until session 2's stored findings are classified, and "provenance unresolved" must stay a class of its own. Classification alone does not authorize reopening; the owner must amend. |
| P2 | Documentation examples: claim-local evidence and revision/coverage gaps (A); incident-based scope and measured pruning (B); invocation vs adherence vs benefit, and optional vs required feedback (C) | None | Codex withdrew its round-1 recommendation. The substance is already in K2(ii), K4, K6, P7 and the record fields. No reader failure is demonstrated, and redundancy is a valid reason to import nothing. The only docs change is labelled citations in BACKLOG #5 and #16. |
| P3 | Review-loop stopping (C: "3 cycles, escalate"; B: a cap in an unshipped skill) | Extend BACKLOG #5 with both sides. No automatic counter | Counter-evidence: A's unlinked anecdote, where a real defect surfaced on review pass 7 after every earlier pass was fixed. B separately rejects numeric question caps. C's three-cycle rule is unmeasured; its eval does not test it. Any K is a cost policy: reaching it establishes neither correctness nor permission to ship. |
| P4 | A behavioural evaluation of haejwo's norm-only obligations with `claude plugin eval` (with vs without the plugin), using A's blinding and C's trace-completeness checks | New BACKLOG #16; it exists only as a proposal | The installed CLI has the command (host-reported FACT). Coverage inside eval children (hooks, SessionStart, the Codex runner) must be established before anything is scored. It needs a predeclared rubric and opportunity denominator, attributed host, plugin, executor and judge revisions, a fixed budget, and incomplete traces kept and reported. `--judge-model` alone establishes no independence. |
| — | Rejected outright | Reject | Whole packs, extra host adapters, fixed swarms, "encode every lesson structurally", repeated workflow confirmations, source-rewriting read hooks, worker-owned pushes, and treating fresh context as independent review. |

## Codex's answer to the owner (verbatim)
"Yes—these repos offer useful evidence and experiment-design techniques that fit haejwo's philosophy. Take labelled corroboration and counter-evidence into #5, plus one conditional evaluation proposal in #16. Import no runtime behavior, reviewer-contract wording, or general documentation examples now. Their usefulness supports examining a candidate; demonstrated need or an informed owner amendment must justify adoption."

## Residual recorded by the host
Codex's suggested #5 trigger reads "K consecutive completed diff-review rounds in which all substantive findings are rejected with recorded reasons". It would not fire on the case that motivated it: in session 2 every finding was accepted and fixed, and the reviewer still returned reject. BACKLOG #5 therefore counts rounds whose reviewer verdict is reject, and marks this as the host's change.

## Owner follow-up (same day)
The owner said to run the experiments and to apply what is worth applying. The two experiments and what came of them are recorded in [BACKLOG.md](BACKLOG.md) #5, #16 and P1's entry (#19), and in the ledger.

Artefacts: maintainer session fa7810bd scratchpad `debate10/` (common.md, codex_brief_r1.md, codex_r1.md, codex_brief_r2.md, codex_r2.md).
