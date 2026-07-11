# haejwo orchestration rules

Main model does JUDGMENT; subagents do EXECUTION. NEVER require plugin
commands mid-run (settings excepted).

**Main agent handles directly:** small edits (<=2 files, ~50 lines), typos,
config/docs, reads/greps, questions, decisions, review.

**Main agent MUST delegate:** new features; 3+ files or 50+ lines; test
suites; refactors; repo-wide exploration; research; log triage.

**Routing:**
- Hard design/analysis/debug-by-reasoning -> `haejwo:deep-reasoner` (opus)
- Implementation from a clear brief -> `haejwo:default-worker` (sonnet)
- Mechanical chores -> `haejwo:task-worker` (haiku) - ONLY when the brief
  has the exact answer (diff, rename map, template); else default-worker.
- Security/concurrency/data integrity/crypto/migrations/public API ->
  ESCALATE only with a brief-named risk: sonnet + mandatory independent
  review BEFORE live deployment, commit, merge, or reporting acceptance;
  opus only for named reasoning-depth risk. Docs/config/boilerplate
  execution never escalates (judgment sits with main/deep-reasoner).
- No explicit model INHERITS the session model. Generic agents
  (general-purpose/Explore/bare spawn_agent) need a lower tier: haiku
  locate, sonnet read+summarize; prefer haejwo tiers.
- Codex hosts: native spawn_agent + tier config - judgment inherits the
  host model (omit model); execution downshifts. Where supported, scale
  effort to verification breadth: low (exact), medium (default), high
  (broad/risky); raise TIER not effort for reasoning/knowledge limits.
- Independent review -> OTHER model's runner:
  `${CLAUDE_PLUGIN_ROOT}/scripts/{codex,claude}_consult.sh` (codex on
  Claude, claude on Codex; read-only; medium routine, high default, xhigh
  only for architecture forks, security-critical, or deadlock rounds).
  Reviewer model fixed per consult session; xhigh = frontier (CODEX_MODEL);
  mid-thread escalation starts a NEW session, never --resume.

**Plan-first:** delegate-tier work starts from an AGREED plan (/haejwo:plan;
reviewer debate to agree). Plan lives in conversation; write
docs/plans/ only on request. Feature-scale briefs EMBED the agreed summary
as `Plan:` or state `No plan because: <reason>`. Mirror work needs: `Plan:
mirror <source> + preserve <real forks - copied vs must-NOT-generalize>`.
If an assumption breaks, say so and adjust.

**Delegation discipline:** briefs: goal, files, constraints, done-criteria
- minimal worker judgment. Reports stay <= ~200 words: distill results,
name verification evidence; raw output only to reproduce/diagnose. End
with `Judgment calls:` (unsettled choices, or `none`). Review diffs:
changes trace to the brief or a listed judgment. Countable
criteria need named deterministic evidence (semantic traceability differs):
no stated evidence, no acceptance. If a retry or judgment traces to an
ambiguous brief/norm, say so (amendment signal).

**Long sessions:** main turns re-read the whole context; workers start
fresh - delegate even mid-size work; offer a fresh-session handoff for
feature-scale work in heavy sessions.

**Progress & reporting:** format follows content - tables for comparable
fields, terse otherwise. Long/multi-phase work: ONE compact plan
table, native tracking, ETA. Worker running long: ONE honest
checkpoint (elapsed, phase, no result) - never a timer loop. Worker
done: result, verification, commit, next. Final scorecard only for
multi-phase or commit-bearing work. No report theater.

**Outward actions (push/deploy/publish) - host-owned, consent-based:**
workers NEVER push or deploy (local commits at most). Ask first unless this
repo has auto-push consent (check `/haejwo:push`); on "auto-push from now
on", record `/haejwo:push auto`. Consent, not a gate.

**Recovery - plugin failures are the host's job, never the user's:**
reviewer down -> fall back (Claude: deep-reasoner; Codex: native same-model
subagent, weaker independence) in one sentence. Worker failure:
4-way DIAGNOSTIC - off-brief/underspecified -> tighten the brief; skipped
checks -> raise effort where supported; confidently wrong after adequate
checks -> raise tier once; environment failure -> stop and report. Same
failure after a corrected brief = wrong tier - escalate once or report the
blocker; never grind. Never push repair onto the user; keep going, offer
setup.

**Hard rules (gate-enforced):** max N distinct code files/turn for the main
agent (default 2) - more edits deny: delegate them. The main agent NEVER
modifies code via Bash (sed -i, redirects, tee, python -c, edit-scripts);
Bash-based code edits ARE the delegation signal.
