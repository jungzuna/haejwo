---
name: haejwo-status
description: Show haejwo's full status (read-only) — config, this turn's edit counter, reviewer readiness, hook observations, and trace anomalies.
---

<!-- MIRROR of commands/status.md for the Codex host — do not edit by hand;
     edit commands/status.md and run `python3 tests/mirrors.py --write`.
     Drift is canary-tested. -->


You are the **haejwo host**. Collect, then report compactly. Run:

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status_collect.py" "${CLAUDE_PLUGIN_DATA}" "$CLAUDE_CODE_SESSION_ID"`

Unsubstituted data dir, by host: Claude Code `~/.claude/plugins/data/haejwo-haejwo/`, Codex `~/.codex/plugins/data/haejwo-haejwo/`. The session id is `$CLAUDE_CODE_SESSION_ID` (not `$CLAUDE_SESSION_ID`). The script is read-only and always exits 0; read its block, never the JSON files yourself. Then interpret:

1. **Config** — `CONFIG MALFORMED` → say so FIRST: hooks fail open until repaired. Absent or `configured=no` → defaults active; suggest `/haejwo:setup`. Flags are the EFFECTIVE state (env override, gate off and malformed config already applied).
2. **This turn** — the counter line as printed; keep its "may be another session's" label when the session id is unknown.
3. **Reviewer** — one line: first the CLI probe (Claude host `codex login status`; Codex host `claude --version` + any login check), then the collector's reviewer line (stored model, effort, sandbox, consent, verification age).
4. **Observations** — the last records as printed. No subagent-origin records = no subagent origin observed, NOT that worker hooks never fire.
5. **Delegations** — counts only. `None` model = no override → agent-file default on Claude Code; Codex: child INHERITS the host model. Label `plan_marker_kind none` "haejwo tiers only; exploration needs no marker". Reviewer runner logs: count `*.reply.log` in the session scratchpad you verified — an ARTIFACT count (overwritten on `-o` reuse; custom `-o` locations excluded); path or session id unknown → "unavailable", NEVER zero as "no consults"; say 'runner logs', never 'consults'. `codex:` rescue delegations are outside haejwo's budget and bash guard — NEVER "ungated writes".
6. **Anomalies (surface only — NEVER propose changes)** — report the printed ones plainly; the host interprets, not the user.

End with the collector's summary line: gate ACTIVE/OFF, budget N, configured yes/no.
