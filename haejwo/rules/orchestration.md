# haejwo orchestration rules

Main model does JUDGMENT (plan, decide, review, accept); subagents do
EXECUTION. NEVER require plugin commands mid-run (settings excepted).

**Handle directly:** small edits (<=2 files, ~50 lines), typos, config/docs, reads,
questions, decisions, review.
**Delegate:** new features; 3+ files or 50+ lines; test suites; refactors;
repo-wide exploration; research; log triage. Session model == worker tier: size
alone never forces delegation (long-session isolation may); gate limit
applies while on.

**Routing:**
- Implementation from a clear brief -> `haejwo:default-worker`.
- Bounded mechanical work with clear expected output and deterministic
  verification, no behavior/risk/API/data-shape judgment ->
  `haejwo:task-worker`; else default-worker.
- Deep analysis / same-model verification -> `haejwo:deep-reasoner`
  (fresh context, NOT independent authority).
- Independent review -> the OTHER vendor's
  `${CLAUDE_PLUGIN_ROOT}/scripts/{codex,claude}_consult.sh`;
  runner default medium (routine), high (plans/diffs), xhigh ONLY
  (architecture forks/security-critical/deadlock). Model fixed; escalate in
  a NEW session, never --resume.
- Risk classes (security/concurrency/data integrity/crypto/migrations/public
  API): escalate only with a brief-named risk + independent review BEFORE
  deploy, commit, merge, or acceptance; docs/config/boilerplate never
  escalates.
- Generic agents (general-purpose/Explore): omitting model inherits;
  explicit model required (gate-enforced). Prefer haejwo tiers.

**Plan-first:** judgment-bearing feature/risk work starts from a
reviewer-critiqued plan: one critique by default; more only on an
unresolved substantive objection or new evidence, naming the decision it
changes; host decides with rationale. Briefs EMBED
`Plan:` or `No plan because: <reason>`; bounded read-only exploration needs
neither.

**Decisions made for the user** (added scope, API removals, security
defaults, migrations): state them and reviewer objections rejected or
deferred, with reasons, BEFORE implementing or delegating; carry unresolved
ones into the final report.

**Briefs & acceptance:** goal, files, constraints, done-criteria; minimal
worker judgment. Accept only diffs tracing to the brief or a disclosed
judgment call; countable criteria need named deterministic evidence; none,
no acceptance. Codex briefs append: verification evidence, concise report,
`Judgment calls:`. Scope-limited review: name reviewed scope and omissions in
the brief and the acceptance report; keep review coverage, verification done
and delivery pending distinct.

**Reporting:** proportional to content; one honest checkpoint, no theater.

**Outward (push/deploy/publish):** host-owned; workers NEVER push or deploy.
Ask first unless `/haejwo:push auto` consent is recorded for the repo.

**Recovery (host-owned):** reviewer down -> disclose same-model fallback
(Claude: deep-reasoner; Codex: native subagent). Worker fails: diagnose,
fix, retry or raise the tier ONCE, else re-brief/decompose; never grind.

**Gate-enforced:** main: max N code files/turn (default 2); more deny ->
delegate; Bash redirect/tee/in-place code writes denied (heuristic).
**Norm:** no code edits via Bash (python -c, scripts too); Bash code edits
ARE the delegation signal.
