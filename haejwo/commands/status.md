---
description: Show haejwo's full status (read-only) — config, this turn's edit counter, reviewer readiness, hook observations, and trace anomalies.
argument-hint: "(no arguments)"
---

You are the **haejwo host**. Report compactly from data dir `${CLAUDE_PLUGIN_DATA}` (unsubstituted → `ls -d ~/.claude/plugins/data/*haejwo*`):

1. **Config** — from `config.json`: configured?, gate, budget, bash-guard, tiers, reviewer enabled/`verified_at`, push auto-repos. Missing → defaults active; suggest `/haejwo:setup`. Malformed JSON → say so FIRST: hooks fail open until repaired.
2. **This turn** — the hooks' state-file name: `$CLAUDE_CODE_SESSION_ID` (not `$CLAUDE_SESSION_ID`), every `[^A-Za-z0-9_-]` → `-`, first 80 chars, read `state/<that>.json`. Report main-agent code edits (n/budget). Unknown session id → say "session id unknown — newest session-state JSON shown, may be another session's"; read the newest `state/*.json` ONLY, never an observations file.
3. **Reviewer** — one line: CLI probe (Claude host `codex login status`; Codex host `claude --version` + any login check), then stored model, effort, sandbox, consent. `verified_at` null/invalid → "never verified"; future → anomaly; >30 days, enabled → "re-run `/haejwo:setup` to re-verify"; disabled → date only.
4. **Observations — this session** — §§4–6 read `state/observations.jsonl.1` (if present) then `.jsonl`, keeping records with `sid` = first 12 chars of `$CLAUDE_CODE_SESSION_ID`; unknown session id → say so, machine-wide line only. Last ~10 chronologically with `decision`/`via`; say if any has non-null `agent_type` — all null = no subagent origin observed, NOT that worker hooks never fire. None → say so. Then ONE line over both files: span, records, distinct sids, denies.
5. **Anomalies (this session; surface only — NEVER propose changes)** — denial streaks (3+ denies), unexpected actor types, absent expected hook fires, new shapes. Report plainly; the host interprets, not the user.
6. **Delegations (this session)** — from matching `"hook":"delegation"` records. Counts only: by `subagent_type`/`requested_model` (null = no override → agent-file default on Claude Code; Codex: child INHERITS the host model), denies incl. `tier_pin_check`, `plan_marker_kind=="none"` with `prompt_bytes>1500` (size proxy, not feature scope) counted and labeled "haejwo tiers only; exploration needs no marker", reviewer runner logs: count `*.reply.log` in the session scratchpad the host verified (Claude Code: /tmp/claude-0/<project-slug>/<session-id>/scratchpad/; Codex: its equivalent if known) — an ARTIFACT count (overwritten on `-o` reuse; custom `-o` locations excluded); session id or path unknown → "unavailable", NEVER zero as "no consults"; say 'runner logs', never 'consults', push-consent registry state, and `codex:`-prefixed rescue delegations separately — outside haejwo's budget and bash guard, NEVER "ungated writes".

End with one line: gate ACTIVE/OFF, budget N, configured yes/no.
