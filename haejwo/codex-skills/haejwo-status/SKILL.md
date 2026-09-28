---
name: haejwo-status
description: Show haejwo's full status (read-only) — config, this turn's edit counter, reviewer readiness, hook observations, and trace anomalies.
---

<!-- MIRROR of commands/status.md for the Codex host — do not edit by hand;
     edit commands/status.md and regenerate. Drift is canary-tested. -->


You are the **haejwo host**. Report full plugin status. Data dir `${CLAUDE_PLUGIN_DATA}` (unsubstituted → `ls -d ~/.claude/plugins/data/*haejwo*`). Present compactly:

1. **Config** — from `config.json`: configured?, gate, budget, bash-guard, tiers, reviewer enabled/`verified_at`, push auto-repos. Missing → defaults are active, suggest `/haejwo:setup`. Malformed JSON → say so FIRST: hooks are fail-open until it is repaired.
2. **This turn** — the state file as the hooks name it: `$CLAUDE_CODE_SESSION_ID` (not `$CLAUDE_SESSION_ID`), every `[^A-Za-z0-9_-]` → `-`, truncated to 80 chars, read `state/<that>.json`. Report the code files the main agent edited this turn (n/budget). Unknown session id → say "session id unknown — showing the newest session-state JSON, which may belong to another session" and take the newest `state/*.json` ONLY, never an observations file.
3. **Reviewer** — one line: the CLI probe (Claude host `codex login status`; Codex host `claude --version` plus any login check), then the stored model, effort, sandbox and consent. `verified_at` null/invalid → "never verified"; future → an anomaly; over 30 days while enabled → "re-run `/haejwo:setup` to re-verify"; disabled → date only.
4. **Observations — this session** — §§4–6 read `state/observations.jsonl.1` (if present) then `.jsonl`, keeping records whose `sid` = the first 12 chars of `$CLAUDE_CODE_SESSION_ID`; unknown session id → say so, machine-wide line only. Last ~10 chronologically with `decision`/`via`; say whether any has a non-null `agent_type` — all null means no subagent origin observed this session, NOT that worker hooks never fire. None → "no observations yet this session". Then ONE line over both files: span, records, distinct sids, denies.
5. **Anomalies (this session; surface only — NEVER propose changes)** — denial streaks (3+ denies), unexpected actor types, absent expected hook fires, shapes not seen before. Report plainly; interpretation is the host's job, never the user's.
6. **Delegations (this session)** — from matching `"hook":"delegation"` records, RAW COUNTS ONLY (no scores/percentages), at most one observation line each: by `subagent_type` and `requested_model` (null = no override recorded → agent-file default on Claude Code; on Codex the child INHERITS the host model), denies incl. `tier_pin_check`, `plan_marker_kind=="none"` with `prompt_bytes>1500` (a size proxy, not feature scope), whether a reviewer consult ran, the push-consent registry state, and `codex:`-prefixed rescue delegations separately — outside haejwo's edit budget and bash guard, NEVER "ungated writes".

End with one line: gate ACTIVE/OFF, budget N, configured yes/no.
