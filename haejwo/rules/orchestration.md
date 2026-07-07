# haejwo orchestration rules

Main model does JUDGMENT; subagents do EXECUTION. The host works automatically:
the user asks; NEVER require plugin commands mid-workflow (settings excepted).

**Main agent handles directly:** small 1-2 file edits (<= ~50 changed lines),
typos, config/docs, single-file reads, quick greps, questions, decisions,
reviewing results.

**Main agent MUST delegate:** new features; 3+ files or 50+ lines; test
suites; refactors; repo-wide exploration; research; log triage.

**Routing:**
- Hard design/analysis/debugging-by-reasoning -> `haejwo:deep-reasoner` (opus)
- Implementation from a clear brief -> `haejwo:default-worker` (sonnet)
- Mechanical chores -> `haejwo:task-worker` (haiku)
- Security/concurrency/data integrity/crypto/migrations/public API ->
  ESCALATE: opus override, or sonnet + mandatory independent adversarial
  review before accepting.
- On Codex hosts: use native spawn_agent + injected codex tier config —
  judgment inherits the host model (omit model); execution downshifts.
- Independent review/outside perspective -> the OTHER model's runner:
  `${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh` on Claude,
  `${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh` on Codex
  (`--mode consult <brief>`, read-only; medium routine, high default, xhigh
  only for architecture forks, security-critical calls, or final deadlock
  rounds).

**Plan-first:** delegate-tier work starts from an AGREED plan — run
/haejwo:plan yourself (reviewer debate to agreement). The plan lives in
conversation; write docs/plans/ only on user request. Feature-scale briefs
EMBED the agreed summary as `Plan:` or state `No plan because: <reason>`.
Mirror work still needs:
`Plan: mirror <source> + preserve <real forks — what's copied, what must NOT
generalize>`. If implementation invalidates an assumption, say so and adjust.

**Delegation discipline:** briefs are self-contained: goal, files,
constraints, done-criteria. Reports stay <= ~200 words and end with
`Judgment calls:` (brief-unsettled behavioral choices, or `none`). Review
diffs before accepting: every behavior change must trace to the brief or a
listed judgment; carry accepted judgments upward. Countable done-criteria
need named deterministic evidence; semantic traceability is separate — no
stated evidence, no acceptance. If a retry or judgment call traces to an
ambiguous brief or norm, say so (amendment signal).

**Progress & reporting:** format follows content — tables for comparable
fields, terse lines otherwise. Long/multi-phase work: ONE compact plan table,
native task tracking, ETA. Worker running long: ONE honest checkpoint
(elapsed, phase, no result yet) — never a timer loop. Worker done: result,
verification, commit, next. Final scorecard only for multi-phase or
commit-bearing work. No report theater.

**Outward actions (push/deploy/publish) — host-owned, consent-based:**
workers NEVER push or deploy (local commits at most). Ask first unless this
repo has auto-push consent (check `/haejwo:push`); on "do it automatically
from now on", record `/haejwo:push auto`. Consent, not a gate.

**Recovery — plugin failures are the host's problem, never the user's:**
reviewer down -> fall back (Claude: deep-reasoner; Codex: native same-model
subagent, weaker independence) with one plain sentence. Worker failure ->
retry once with a tighter brief, then report plainly. NEVER ask the user to
repair the plugin mid-request; finish what is possible, then offer setup.

**Hard rules (gate-enforced):** max N distinct code files edited by the main
agent per turn (default 2) — further edits are denied: delegate them. The
main agent NEVER modifies code via Bash (sed -i, redirects, tee, `python -c`,
edit-scripts); reaching for Bash to change code IS the delegation signal.
