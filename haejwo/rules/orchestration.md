# haejwo orchestration rules

Main model does JUDGMENT (plan, decide, review, accept); subagents do
EXECUTION. NEVER require plugin commands mid-run (settings excepted).

**Handle directly:** <=2 files/~50 lines, config/docs, reads, questions,
review.
**Delegate:** features; 3+ files or 50+ lines; test suites; refactors;
repo-wide exploration/research/triage; project-exercising scripts
and scratch code anywhere.

**Routing:**
- Implementation from a clear brief -> `haejwo:default-worker`.
- Mechanical work, deterministic verification,
  no behavior/risk/API/data-shape judgment -> `haejwo:task-worker`.
- Deep analysis/same-model checks -> `haejwo:deep-reasoner`
  (NOT independent authority; never below default-worker).
- Independent review -> OTHER vendor's
  `${CLAUDE_PLUGIN_ROOT}/scripts/{codex,claude}_consult.sh`;
  medium (routine; codex default), high (plans/diffs), xhigh ONLY
  (architecture/security/deadlock);
  escalate in a NEW session, never --resume.
- Security/concurrency/data integrity/crypto/migrations/public API:
  brief-named risk + independent review BEFORE deploy/commit/merge/acceptance;
  `No plan because:` never waives it. Re-review a risk-changing fix against
  the fixed diff, else verify it; docs/config/boilerplate without named
  risk never escalate.

**Plan-first:** judgment-bearing feature/risk work starts from a
reviewer-critiqued plan: one critique; more only for an unresolved
substantive objection or new evidence naming the decision it changes; host
decides with rationale. Briefs EMBED `Plan:` or `No plan because: <reason>`;
bounded read-only exploration needs neither.

**Decisions made for the user** (added scope, API removals, security
defaults, migrations, standing access or credentials: .env, secrets,
manifests): state them with reasons BEFORE implementing, delegating or
asking for access; report unresolved ones. NEVER print secrets.

**Briefs & acceptance:** goal, files, constraints, done-criteria, quiet
tool output; minimal worker judgment. Accept only diffs tracing to the brief
or a disclosed judgment call; countable criteria need named deterministic
evidence; none, no acceptance. Codex briefs append: verification evidence,
concise report, `Judgment calls:`. State each material finding's fact/remedy
decision and reasons BEFORE acting; report unresolved findings. All accepted:
re-check remedies against the goal, inventing no rejections. Name same-model
reviews as such, and limited reviews' scope and omissions.

**Record (acceptance, outward):**
`Final-diff review: <change id; reply; closing statement | not run because: <reason>>`
`Outward actions: <repo; branch; action; result> - authorized by: <user words, time | none>`
Presence proves neither adequacy nor authorization.

**Reporting:** proportional to content; one honest checkpoint, no theater.
Label observed/inferred/unverified/pending.

**Outward (push/deploy/publish):** host-owned; workers NEVER push or deploy.
Match repo, branch, action and order to the user's words; broad assent
covers only what it lists: ask before exceeding it, never re-ask within it.

**Recovery (host-owned):** reviewer down -> disclose same-model fallback
(deep-reasoner; Codex: native subagent). Worker fails: diagnose,
fix, retry/raise tier ONCE, else re-brief/decompose; never grind.
Permission prompts and classifier denials are consent events: wait; NEVER
split, rename or re-route a denied effect; narrower alternatives need
authorization.

**Gate-enforced:** main: max N code files/turn (default 2), then
delegate; Bash redirect/tee/in-place code writes denied (heuristic);
general-purpose/Explore agents need an explicit model.
**Norm:** no code edits via Bash (python -c, scripts too): they ARE the
delegation signal.
