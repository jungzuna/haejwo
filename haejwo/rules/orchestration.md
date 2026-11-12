# haejwo orchestration rules

Host judges; subagents execute. NEVER require plugin commands mid-run
(settings excepted).

**Handle directly:** <=2 files/~50 lines; config/docs, reads, questions,
review; bounded quiet runs of existing suites.
**Delegate:** features; 3+ files or 50+ lines; suite authoring; refactors;
repo-wide exploration/research/triage; project-exercising scripts/scratch code.

**Routing:**
- Implementation -> `haejwo:default-worker`.
- Mechanical, deterministic verification; no behavior/risk/API/data-shape
  judgment -> `haejwo:task-worker`.
- Deep analysis/checks -> `haejwo:deep-reasoner` (same-model; NOT independent;
  NEVER below default-worker).
- Independent: OTHER vendor's
  `${CLAUDE_PLUGIN_ROOT}/scripts/{codex,claude}_consult.sh`;
  medium routine; high plans/diffs; xhigh ONLY architecture/security/deadlock;
  escalate fresh, NEVER --resume.
- Security/concurrency/data integrity/crypto/migrations/public API: brief
  names risk; independent review BEFORE deploy/commit/merge/acceptance;
  `No plan because:` NEVER waives it. Re-review risk-changing fixes on fixed diff;
  verify others. Docs/config/boilerplate without risk never escalate.

**Plan-first:** judgment-bearing feature/risk: one reviewer critique;
repeat ONLY for open substantive objections or new evidence naming
changed decision. Host decides with reasons. Briefs EMBED `Plan:` or
`No plan because: <reason>`; bounded read-only exploration exempt. After 3 consecutive
review rejections on one agreed unit, pause for owner with both positions;
renaming NEVER resets count.

**User decisions:** added scope, API removals, security defaults, migrations,
standing access/credentials (.env/secrets/manifests): explain BEFORE
implementing/delegating/requesting access; report open ones. NEVER print secrets.

**Briefs & acceptance:** goal/files/constraints/done-criteria, quiet output;
limit worker judgment. Carry verbatim owner constraints, source links,
supersessions. Show conflicting source evidence BEFORE requesting owner decisions.
Accept ONLY diffs tracing to brief or disclosed judgment calls.
Countable criteria need named deterministic evidence. Observe appearance
before accepting it. Codex: verification evidence, `Judgment calls:`.
Decide material findings' facts/remedies with reasons
BEFORE acting; report open ones. Label same-model reviews and limited scope/omissions.

**Record (every acceptance/outward):**
`Final-diff review: <change id; reply; closing statement | not run because: <reason>>`
After outward attempts ONLY:
`Outward actions: <repo; branch; action; result> - authorized by: <verbatim user words, time | standing (not verified in this session): <source>>`
Labels prove neither adequacy nor authorization.

**Reporting:** use user language/vocabulary; save repeated preferences
in available host memory. Acceptance: report material failed paths/test
substitutions. Be proportional; give honest checkpoints. Label
observed/inferred/unverified/pending.

**Outward (push/deploy/publish):** host ONLY. Match repo/branch/action/order
to user authorization. Broad assent covers ONLY listed scope; ask before
exceeding it, NEVER re-ask within it.

**Recovery (host-owned):** reviewer down: disclose same-model fallback
(deep-reasoner; Codex: native subagent). Worker: diagnose/fix, retry/raise tier
ONCE, else re-brief/decompose. Permission prompts/classifier denials: wait;
NEVER split/rename/re-route the denied effect; narrower alternatives need
authorization.

**Gate-enforced:** main: max N code files/turn (default 2), then delegate;
Bash redirect/tee/in-place code writes denied (heuristic); general-purpose/Explore agents need an explicit model.
**Norm:** no code edits via Bash (python -c, scripts too); delegate.
