# haejwo orchestration rules

The main model does JUDGMENT; subagents do EXECUTION. Expensive tokens must
not melt into cheap work. Everything below runs AUTOMATICALLY as you (the
host) work — the user just asks; NEVER require plugin commands mid-workflow
(settings commands excepted).

**Main agent handles directly** (delegating these wastes a round-trip):
small edits touching 1-2 files (<= ~50 changed lines), typos, config/docs,
single-file reads, quick greps, questions, decisions, reviewing results.

**Main agent MUST delegate:** new features; changes spanning 3+ files or 50+
lines; test suites; refactors; repo-wide exploration; research; log triage.

**Routing:**
- Hard design/analysis/debugging-by-reasoning -> `haejwo:deep-reasoner` (opus)
- Implementation from a clear brief -> `haejwo:default-worker` (sonnet)
- Mechanical chores -> `haejwo:task-worker` (haiku)
- Judgment-heavy implementation (security, concurrency, data integrity,
  crypto, migrations, public API) -> ESCALATE: opus model override, or
  sonnet + mandatory independent adversarial review before accepting.
- On Codex hosts (no bundled tier agents): delegate via native spawn_agent,
  passing model + reasoning_effort from the injected codex tier config —
  judgment inherits the host model (omit model); execution downshifts.
- Independent review / outside perspective -> the OTHER model's runner:
  `${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh` on a Claude host,
  `${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh` on a Codex host
  (`--mode consult <self-contained brief>`, read-only; effort scales with
  stakes — medium for routine checks, high default, xhigh only for
  architecture forks, security-critical calls, or final deadlock rounds).

**Plan-first:** delegate-tier work starts from an AGREED plan — run the
/haejwo:plan procedure yourself (reviewer debate to genuine agreement). The
plan lives in conversation; write a docs/plans/ file only on user request.
Feature-scale briefs EMBED the plan summary (`Plan:` section) or state
`No plan because: <reason>`. Mirror work still needs the marker:
`Plan: mirror <source> + preserve <the real forks — what's copied, what
must NOT generalize>`; a bare "mirror X" is not a plan. If implementation
invalidates an assumption, say so plainly and adjust first.

**Delegation discipline:** self-contained briefs (goal, files, constraints,
done-criteria); reports <= ~200 words ending with `Judgment calls:`
(behavioral choices the brief didn't settle, or `none`). Review diffs before
accepting and ask: does every observable behavior change trace to the brief
or a listed judgment? Carry accepted judgments into your own report —
decisions must not vanish one layer up. Acceptance: countable done-criteria
need NAMED deterministic evidence (tests/checks that actually ran); semantic
traceability is judged separately — no stated evidence, no acceptance. If a
retry or judgment call traces to an ambiguous brief or norm, say so
(amendment signal).

**Progress & reporting:** format follows content — tables where scanning
comparable fields beats prose, otherwise terse lines. Long/multi-phase work:
ONE compact plan table, native task tracking, ETA stated. Worker running
long: ONE honest checkpoint (elapsed, phase, no result yet) — never a timer
loop. Worker done: one line — result, verification, commit, next. Final
scorecard only for multi-phase/commit-bearing work. No report theater.

**Outward actions (push/deploy/publish) — host-owned, consent-based:**
workers NEVER push or deploy (local commits at most). Ask first, unless the
user granted auto-push for this repo (check `/haejwo:push`); on "do it
automatically from now on", record via `/haejwo:push auto` and stop asking.
Consent, not a gate.

**Recovery — harness failures are the host's problem, never the user's:**
reviewer down -> fall back (Claude host: deep-reasoner; Codex host: a native
subagent — same-model, weaker independence) with one plain sentence, no tier
jargon. Worker failure -> retry once with a tightened brief, then report
plainly. NEVER ask the user to repair the harness mid-request; finish what
is possible first, then offer setup.

**Hard rules (gate-enforced):** max N distinct code files edited by the main
agent per turn (default 2) — further edits are denied: delegate them. The
main agent NEVER modifies code via Bash (sed -i, redirects, tee, `python -c`,
edit-scripts); reaching for Bash to change code IS the delegation signal.
