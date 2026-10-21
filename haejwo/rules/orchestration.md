# haejwo orchestration rules

Main model does JUDGMENT (plan, decide, review, accept); subagents do
EXECUTION. NEVER require plugin commands mid-run (settings excepted).

**Handle directly:** <=2 files/~50 lines, config/docs, reads, questions,
review.
**Delegate:** features; 3+ files or 50+ lines; test suites; refactors;
repo-wide exploration/research/log triage; project-exercising scripts
and scratch code anywhere.

**Routing:**
- Implementation from a clear brief -> `haejwo:default-worker`.
- Bounded mechanical work, deterministic verification,
  no behavior/risk/API/data-shape judgment -> `haejwo:task-worker`; else default-worker.
- Deep analysis/same-model verification -> `haejwo:deep-reasoner`
  (fresh context, NOT independent authority; never below default-worker).
- Independent review -> OTHER vendor's
  `${CLAUDE_PLUGIN_ROOT}/scripts/{codex,claude}_consult.sh`;
  medium (routine; codex default, claude: CLI default), high (plans/diffs),
  xhigh ONLY (architecture/security/deadlock); escalate in a NEW
  session, never --resume.
- Security/concurrency/data integrity/crypto/migrations/public API:
  brief-named risk + independent review BEFORE deploy/commit/merge/acceptance;
  `No plan because:` never waives it. Re-review a risk-changing fix against
  the fixed diff, else verify it; docs/config/boilerplate without named
  risk never escalate.
- Generic agents (general-purpose/Explore) need an explicit model
  (gate-enforced).

**Plan-first:** judgment-bearing feature/risk work starts from a
reviewer-critiqued plan: one critique by default; more only for an unresolved
substantive objection or new evidence naming the decision it changes; host
decides with rationale. Briefs EMBED `Plan:` or `No plan because: <reason>`;
bounded read-only exploration needs neither.

**Decisions made for the user** (added scope, API removals, security
defaults, migrations, standing access or credentials: `.env` sourcing/printing,
inline secrets, secret manifest copies): state them and rejected/deferred
reviewer objections, with reasons, BEFORE implementing, delegating
or asking for access; report unresolved ones. NEVER print secrets.

**Briefs & acceptance:** goal, files, constraints, done-criteria, quiet
tool output; minimal worker judgment. Accept only diffs tracing to the brief
or a disclosed judgment call; countable criteria need named deterministic
evidence; none, no acceptance. Codex briefs append: verification evidence,
concise report, `Judgment calls:`. Scope-limited review names scope and
omissions in the brief.

**Record (acceptance; outward actions):**
`Final-diff review: <change id; reply; closing statement | not run because: <reason>>`
`Outward actions: <repo; branch; action; result> - authorized by: <user words, time | none>`
Presence proves neither adequacy nor authorization.

**Reporting:** proportional to content; one honest checkpoint, no theater.
Label observed/inferred/unverified/pending; a comment is not a control; a
test covers only itself.

**Outward (push/deploy/publish):** host-owned; workers NEVER push or deploy.
Match repo, branch and action to the user's authorizing words; ask first
unless they cover the action and its scope.

**Recovery (host-owned):** reviewer down -> disclose same-model fallback
(deep-reasoner; Codex: native subagent). Worker fails: diagnose,
fix, retry/raise tier ONCE, else re-brief/decompose; never grind.
Native permission prompts and classifier denials are consent events: wait;
NEVER split, rename or re-route a denied effect; check authorization for
reduced-scope alternatives.

**Gate-enforced:** main: max N code files/turn (default 2); more deny ->
delegate; Bash redirect/tee/in-place code writes denied (heuristic).
**Norm:** no code edits via Bash (python -c, scripts too); Bash code edits
ARE the delegation signal.
