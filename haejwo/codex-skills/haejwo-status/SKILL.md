---
name: haejwo-status
description: Show haejwo's full status (read-only) — config, this turn's edit counter, reviewer readiness, hook observations, and trace anomalies.
---

<!-- MIRROR of commands/status.md for the Codex host — do not edit by hand;
     edit commands/status.md and regenerate. Drift is canary-tested. -->


You are the **haejwo host**. Report full plugin status. Data dir `${CLAUDE_PLUGIN_DATA}` (unsubstituted → `ls -d ~/.claude/plugins/data/*haejwo*`). Present compactly:

1. **Config** — from `config.json`: configured?, gate, budget, bash-guard, tiers, reviewer enabled/`verified_at`, push auto-repos. Missing → defaults are active, suggest `/haejwo:setup`. Malformed JSON → say so FIRST: hooks are fail-open until it is repaired (2.12).
2. **This turn** — the state file as the hooks name it: `$CLAUDE_CODE_SESSION_ID` (not `$CLAUDE_SESSION_ID`), every `[^A-Za-z0-9_-]` → `-`, truncated to 80 chars, read `state/<that>.json`. Report the code files the main agent edited this turn (n/budget). Unknown session id → say "session id unknown — showing the newest session-state JSON, which may belong to another session" and take the newest `state/*.json` ONLY, never an observations file.
3. **Reviewer** — one line: the CLI probe (Claude host `codex login status`; Codex host `claude --version` plus any login check), then the stored model, effort, sandbox and consent. `verified_at` null/invalid → "never verified"; future → an anomaly; over 30 days while enabled → a quiet "re-run `/haejwo:setup` to re-verify"; disabled → the date only, no nudge.
4. **Observations** — `state/observations.jsonl.1` (if present) then `state/observations.jsonl`, oldest-first, last ~10; report whether any record carries a non-null `agent_type` (subagent-originated) — all null means none observed in this window, NOT that hooks never fire in workers. Prefer the recorded `decision`/`via` fields (2.11+) over the actor field alone.
5. **Anomalies (surface only — NEVER propose changes)** — scan the whole file for unexpected actor types, denial streaks (3+ denies in one session), absent expected hook fires, or shapes not seen before. Report plainly; interpreting them is your job as host, never the user's.
6. **Delegations** — from `"hook":"delegation"` records, RAW COUNTS ONLY (no scores, no percentages), at most one line of observation each: by `subagent_type` and `requested_model` (null = no override recorded → agent-file default on Claude Code; on Codex the child INHERITS the host model), denies, `tier_pin_check` denies, per-hook `decision` counts labeled with the window's time span, `plan_marker_kind=="none"` with `prompt_bytes>1500` (a size proxy, not feature scope), whether a reviewer consult ran this session, the push-consent registry state, and `codex:`-prefixed rescue delegations separately — they run outside haejwo's edit budget and bash guard, and are NEVER called "ungated writes".

End with one line: gate ACTIVE/OFF, budget N, configured yes/no.
