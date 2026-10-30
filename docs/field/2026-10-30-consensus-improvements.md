# After field session 1 — is anything in haejwo still worth changing? (Claude × Codex, 2026-10-30)

Owner's question: "The stabilization release was supposed to be the last, yet problems still appear. Is that unavoidable? Debate with GPT whether anything in haejwo should still be improved."
One round (Codex, effort high); the host adopted every disposition, so no second round was needed. Inputs: the signed field-session-1 consensus (F1–F8), BACKLOG #8–#14, the deny reproduction, the governing K1–K9 and P7/P13.

## Dispositions (candidate → decision, governing clause)
| # | Candidate | Decision | Why (evidence level) |
|---|---|---|---|
| C1 | Bash guard: skip quoted heredoc bodies in the write scan (#14) | BACKLOG WITH REJECT | the one-line fix flips 6/10 (Codex) or 7/10 (same-vendor) pinned adversarial cases — the 2.26 signed stop condition either way (FACT); "only a shell parser can work" is stronger than demonstrated (INFERENCE). P7, K4 |
| C2 | Runner note: literal-assignment resolver for the NOTE matcher only (#15) | BACKLOG WITH REJECT | the wrong-deny withdrawal reason does not transfer to a note (FACT), but the benefit for omission carry-over is SPECULATION; 400 chars bounds neither attention nor maintenance; no automatic 2.36 — reopening or the owner's explicit amendment must precede it. K1, K3, K4–K5 |
| C3 | Plan marker only in a referenced brief file → record `none` | DOCS AT ACCEPTANCE | one README clarification: the record scans the Agent prompt itself; markers only in referenced files are invisible. No worker obligation added. K6, P11 |
| C4 | Credential-use disclosure (`.env` sourcing 0/13) — plugin note or deny | REJECT (this attempt) | a post-use note cannot satisfy a before-use obligation; syntax detection cannot honestly announce "sourced" (a conditional segment may never run); a deny is a tightening under K3. The project-level Claude Code permission deny rule is an owner-process candidate (K7) whose effectiveness is UNVERIFIED until measured on the installed host against compound commands and the supported access paths. K3, K7, P7 |
| C5 | Reviewer omissions carried into reports (0/2) | REJECT a separate mechanism | co-occurred with the absent notes; causality is SPECULATION; do not imply C2 solves it. K1, K3 |
| C6 | Fixed-diff re-review 8/9; reasoned per-finding 21/24 | DOCS AT ACCEPTANCE | attributed rows alongside the separately disclosed un-reviewed alias fix; the figures certify neither decision timing nor remedy quality. K2, K6 |
| C7 | Count scratch scripts under temp paths toward the budget | REJECT | the temp exemption is declared; direct scratch implementation stays a norm omission; the owner-requested deliverable must be distinguished from discretionary probes. P13 needs independent review, not a second incident — one session can supply field evidence. K3–K4 |
| C8 | Host version 2.1.314 outside the measured matrix | DOCS AT ACCEPTANCE | add the row exactly as F2 narrows it; Stop BLOCK and edit-budget denial stay unverified there. K2, K4/T2, P7 |
| C9 | Status reading only the newest observation rotations | REJECT a code change | `load_observations` reads `.3`, `.2`, `.1`, then current (FACT, four-rotation fixture passed); the error was the maintainer's own evaluation process. K6, F7 |
| C10 | Documentation drift the host missed | DOCS (done 2026-10-30 in this folder; the README part at acceptance) | #12–#13 carried pre-reconciliation denominators and the host's wrong "P13 needs more than one session" rationale; #14 and the ledger repeated the unreproduced seven-flip count. Corrected: #12 (13/14), #13 (0/13; P13 = evidence AND independent review), #14 and the ledger (6–7 of 10, two reproductions), #15 added. K6, P7 |

## Signed
- **G1.** No T1/T2 is established by the supplied evidence.
- **G2.** Norm omissions remain consequential owner-process findings.
- **G3.** Acceptance remains conditional under K5/F6.

## Codex's answer to the owner's question (verbatim)
"Recurrence remains possible, but today's evidence does not establish an unavoidable frequency or excuse preventable host omissions. One bounded documentation correction is worth completing; another plugin mechanism has not cleared the agreed reopening bar."

## Residual disagreement recorded
- No automatic 2.36 for notes (the host had floated "build after acceptance"; withdrawn — it is the owner's amendment to make, with a canary and cross-vendor review of the grammar).
- The project permission rule is not a "proven control" until measured (the host had called it a real control; narrowed to "a candidate control, unverified here").
- Seven-flip arithmetic: Codex reproduced six; both counts recorded.

## What this means for the owner
- Nothing in haejwo's code changes now. The one patch still planned is documentation-only (2.35.2 after session 2 and K5 closure): the 2.1.314 measured-path row, the norm-only table rows with their sources, the runner-variable and plan-marker-prompt clarifications (#12 + C3).
- Two things can be changed outside haejwo if the owner wants them: a project permission deny rule for `.env` access (to be measured before trusting it), and the owner's own prompt norms for the host (state credential use; delegate probes).
- #15 (note-only resolver) and #14 (heredoc-aware write scan) stay on the backlog with a reject option; either is reopened only by T1/T2 or by the owner saying so.

Artefacts: session 4c990b2c scratchpad `debate8/` (brief_r1.md, codex_r1.md).
