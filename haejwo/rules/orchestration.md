# haejwo orchestration rules

Main model does JUDGMENT (plan, decide, review, accept); subagents do
EXECUTION. NEVER require plugin commands mid-run (settings excepted).

**Handle directly:** small edits (<=2 files, ~50 lines), typos, config/docs,
reads/greps, questions, decisions, review.
**Delegate:** new features; 3+ files or 50+ lines; test suites; refactors;
repo-wide exploration; research; log triage.

**Routing:**
- Implementation from a clear brief -> `haejwo:default-worker`.
- Bounded mechanical work with clear expected output and deterministic
  verification, no behavior/risk/API/data-shape judgment ->
  `haejwo:task-worker`; else default-worker.
- Isolated deep analysis or same-model verification -> `haejwo:deep-reasoner`
  (fresh context, NOT independent authority).
- Independent review / co-analysis -> the OTHER vendor's runner:
  `${CLAUDE_PLUGIN_ROOT}/scripts/{codex,claude}_consult.sh` (non-editing;
  post-run change detection). Effort: runner default medium for routine
  confirmations, explicit high for plan/design consensus and diff reviews,
  xhigh ONLY for architecture forks, security-critical work, deadlock rounds;
  model fixed per session; escalation = NEW session, never --resume.
- Risk classes (security/concurrency/data integrity/crypto/migrations/public
  API): escalate only with a brief-named risk + independent review BEFORE
  deploy, commit, merge, or reported acceptance; docs/config/boilerplate
  never escalates.
- Generic agents (general-purpose/Explore) INHERIT the session model — on
  Claude pass an explicit cost-appropriate model; prefer haejwo tiers. Codex:
  spawn_agent omits model only for `inherit`, else the configured model +
  reasoning_effort (fresh/partial forks only).

**Plan-first:** material judgment-bearing feature/risk work starts from an
AGREED plan (reviewer debate; the host runs it proactively). Briefs EMBED it
as `Plan:` or `No plan because: <reason>`, which alone suffices for
mechanical/bounded-research work.

**Briefs & acceptance:** goal, files, constraints, done-criteria — minimal
worker judgment. Accept only diffs tracing to the brief or a disclosed
judgment call; countable criteria need named deterministic evidence — none,
no acceptance. Codex briefs append: verification evidence, concise report,
`Judgment calls:`.

**Long sessions:** workers start fresh, main re-reads everything — delegate
even mid-size work; offer a fresh-session handoff when heavy.

**Reporting:** proportional to content — one honest checkpoint, no theater.

**Outward (push/deploy/publish):** host-owned; workers NEVER push or deploy.
Ask first unless the repo has auto-push consent (`/haejwo:push auto` records
it).

**Recovery (host's job, never the user's):** reviewer down -> one-sentence
fallback (Claude: deep-reasoner isolated critique, host stays sole
authority; Codex: native subagent). Worker failure: diagnose brief/skipped
checks/tier/environment -> fix, retry or escalate tier ONCE (no higher tier:
re-brief or decompose); never grind.

**Hard rules (gate-enforced):** max N distinct code files/turn for the main
agent (default 2); more deny -> delegate. The main agent NEVER modifies code
via Bash (sed -i, redirects, tee, python -c); Bash code edits ARE the
delegation signal.
