# haejwo orchestration rules

Main model does JUDGMENT (plan, decide, review, accept); subagents do
EXECUTION. NEVER require plugin commands mid-run (settings excepted).

**Handle directly:** small edits (<=2 files, ~50 lines), typos, config/docs,
reads/greps, questions, decisions, review.
**Delegate:** new features; 3+ files or 50+ lines; test suites; refactors;
repo-wide exploration; research; log triage.

**Routing:**
- Implementation from a clear brief -> `haejwo:default-worker`.
- Mechanical chores -> `haejwo:task-worker` ONLY when the brief has the exact
  answer (diff, rename map, template); else default-worker.
- Isolated deep analysis or same-model verification -> `haejwo:deep-reasoner`
  (fresh context, NOT independent authority — judgment stays with the host).
- Independent review -> the OTHER vendor's runner:
  `${CLAUDE_PLUGIN_ROOT}/scripts/{codex,claude}_consult.sh` (non-editing;
  post-run change detection). Effort medium routine / high default /
  xhigh ONLY for architecture forks, security-critical, deadlock rounds; model
  fixed per session; escalation = NEW session, never --resume.
- Risk classes (security/concurrency/data integrity/crypto/migrations/public
  API): escalate only with a brief-named risk + independent review BEFORE
  live deployment, commit, merge, or reporting acceptance; docs/config/
  boilerplate never escalates.
- Generic agents (general-purpose/Explore/bare spawn_agent) INHERIT the
  session model — pass a cheaper model explicitly; prefer haejwo tiers.
  Codex hosts: spawn_agent judgment inherits (omit model); execution
  downshifts to configured tiers.

**Plan-first:** material judgment-bearing feature/risk work starts from an
AGREED plan (reviewer debate; the host runs it proactively). Briefs EMBED it
as `Plan:` or state `No plan because: <reason>`; mechanical/bounded-research
work: `No plan because:` suffices.

**Briefs & acceptance:** briefs: goal, files, constraints, done-criteria —
minimal worker judgment. Accept only diffs tracing to the brief or a
disclosed judgment call; countable criteria need named deterministic
evidence — none, no acceptance. Codex briefs append: verification evidence,
concise report, `Judgment calls:`.

**Long sessions:** workers start fresh, main re-reads everything — delegate
even mid-size work; offer a fresh-session handoff when heavy.

**Reporting:** proportional to content — one honest checkpoint, no theater.

**Outward (push/deploy/publish):** host-owned, consent-based; workers NEVER
push or deploy. Ask first unless the repo has auto-push consent
(`/haejwo:push auto` records it).

**Recovery (host's job, never the user's):** reviewer down -> one-sentence
fallback (Claude: deep-reasoner isolated critique, host stays sole
authority; Codex: native subagent). Worker failure: diagnose brief/skipped
checks/tier/environment -> fix, retry once or escalate tier once; never
grind.

**Hard rules (gate-enforced):** max N distinct code files/turn for the main
agent (default 2); more deny -> delegate. The main agent NEVER modifies code
via Bash (sed -i, redirects, tee, python -c); Bash code edits ARE the
delegation signal.
