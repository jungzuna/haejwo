# haejwo orchestration rules

Main model does JUDGMENT (plan, decide, review, accept); subagents do
EXECUTION. NEVER require plugin commands mid-run (settings excepted).

**Handle directly:** small edits (<=2 files, ~50 lines), typos, config/docs, reads,
questions, decisions, review.
**Delegate:** new features; 3+ files or 50+ lines; test suites; refactors;
repo-wide exploration; research; log triage. Session model == worker tier: size
alone never forces delegation (long-session isolation may); the gate
limit applies while on.

**Routing:**
- Implementation from a clear brief -> `haejwo:default-worker`.
- Bounded mechanical work with clear expected output and deterministic
  verification, no behavior/risk/API/data-shape judgment ->
  `haejwo:task-worker`; else default-worker.
- Deep analysis / same-model verification -> `haejwo:deep-reasoner`
  (fresh context, NOT independent authority).
- Independent review / co-analysis -> the OTHER vendor's runner:
  `${CLAUDE_PLUGIN_ROOT}/scripts/{codex,claude}_consult.sh`. Effort: runner default medium for routine
  checks, high for plan critiques and diff reviews,
  xhigh ONLY for architecture forks, security-critical work, deadlock rounds;
  model fixed per session; escalation = NEW session, never --resume.
- Risk classes (security/concurrency/data integrity/crypto/migrations/public
  API): escalate only with a brief-named risk + independent review BEFORE
  deploy, commit, merge, or acceptance; docs/config/boilerplate never
  escalates.
- Generic agents (general-purpose/Explore) INHERIT the session model — pass
  an explicit cost-appropriate model; prefer haejwo tiers. Codex: spawn_agent
  omits model only for `inherit`, else configured model +
  reasoning_effort (fresh/partial forks).

**Plan-first:** judgment-bearing feature/risk work starts from a
reviewer-critiqued plan: one critique by default; more only on an
unresolved substantive objection or new evidence, naming the decision it
changes; host decides with rationale, dissent recorded. Briefs EMBED
`Plan:` or `No plan because: <reason>` (enough for bounded-research work).

**Briefs & acceptance:** goal, files, constraints, done-criteria; minimal
worker judgment. Accept only diffs tracing to the brief or a disclosed
judgment call; countable criteria need named deterministic evidence — none,
no acceptance. Codex briefs append: verification evidence, concise report,
`Judgment calls:`.

**Long sessions:** workers start fresh, main re-reads all — delegate even
mid-size work; offer a fresh-session handoff if heavy.

**Reporting:** proportional to content — one honest checkpoint, no theater.

**Outward (push/deploy/publish):** host-owned; workers NEVER push or deploy.
Ask first unless `/haejwo:push auto` consent is recorded for the repo.

**Recovery (host's job, never the user's):** reviewer down -> one-line
fallback (Claude: deep-reasoner critique; Codex: native subagent). Worker
failure: diagnose brief/checks/tier/env -> fix, retry or escalate
tier ONCE (none higher: re-brief or decompose); never grind.

**Hard rules (gate-enforced):** max N distinct code files/turn for the main
agent (default 2); more deny -> delegate. Main agent NEVER edits code via
Bash (sed -i, redirects, tee, python -c); Bash code edits ARE the
delegation signal.
