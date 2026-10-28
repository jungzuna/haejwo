---
name: haejwo-status
description: Show haejwo's full status (read-only) — config, this turn's edit counter, reviewer readiness, hook observations, and trace anomalies.
---

<!-- MIRROR of commands/status.md for the Codex host — do not edit by hand;
     edit commands/status.md and run `python3 tests/mirrors.py --write`.
     Drift is canary-tested. -->


You are the **haejwo host**. Run, then report compactly:

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status_collect.py" "${CLAUDE_PLUGIN_DATA}" "$CLAUDE_CODE_SESSION_ID"`

Both placeholders are substituted by the host for THIS plugin (measured on Claude Code; not on Codex). One UNRESOLVED (literal `${…}`) → STOP before any write; use the host's fallback — data: Claude Code `~/.claude/plugins/data/haejwo-haejwo/`, Codex `~/.codex/plugins/data/haejwo-haejwo/`; root: this file's plugin directory. Read the script's block, never the JSON files yourself. Then interpret:

1. **Config** — `CONFIG MALFORMED` → say so FIRST: hooks fail open until repaired. Absent or `configured=no` → stored gate values apply even before setup; suggest `@haejwo-setup`. Flags are the EFFECTIVE state (overrides already applied).
2. **This turn** — the counter line as printed, label included.
3. **Reviewer** — one line: first the CLI probe (Claude host `codex login status`; Codex host `claude --version` + any login check), then the collector's reviewer line.
4. **Observations** — last records as printed (pruned by size, ~800 KB, four files, not by age). No subagent-origin records ≠ worker hooks never fire. `handback_nudge` / `turn_checklist` counts = 2.33.0 records of hooks removed in 2.34.0.
5. **Delegations** — counts only. `None` model = no override → agent-file default on Claude Code; Codex: child INHERITS the host model. Label `plan_marker_kind none` "recorded non-rescue delegations, including generic agents; a missing marker is not necessarily a violation". Reviewer runner logs: count `*.reply.log` in the session scratchpad you verified — an ARTIFACT count (overwritten on `-o` reuse; custom `-o` locations excluded); path or session id unknown → "unavailable", NEVER zero; say 'runner logs', never 'consults'. `codex:` rescue delegations are outside haejwo's budget and bash guard — NEVER "ungated writes".
6. **Anomalies (surface only — NEVER propose changes)** — report the printed ones plainly.

End with the collector's summary line.
