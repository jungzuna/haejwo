---
name: haejwo-status
description: Show haejwo's full status (read-only) — config, this turn's edit counter, reviewer readiness, hook observations, and trace anomalies.
---

<!-- MIRROR of commands/status.md for the Codex host — do not edit by hand;
     edit commands/status.md and regenerate. Drift is canary-tested. -->


You are the **haejwo host**. Report full plugin status. Data dir: `${CLAUDE_PLUGIN_DATA}` (if unsubstituted: `ls -d ~/.claude/plugins/data/*haejwo*`).

Gather and present compactly:
1. **Config** — `config.json` (configured?, gate on/off, budget, bash-guard, model tiers, codex enabled/verified_at, push auto-repos). If missing: say defaults are active and suggest `/haejwo:setup`.
2. **This turn** — newest file in `state/`: which code files the main agent has edited this turn (n/budget).
3. **Reviewer CLI** — on a Claude host: `codex login status`; on a Codex host: `claude --version` plus any available login check. Report one line.
4. **Observations** — last ~10 lines of `state/observations.jsonl`; specifically report whether any record has a non-null `agent_type` (= hooks DO fire inside subagents and exemption is active) or all null (= hooks fire only for the main agent). This is the live evidence for the gate's subagent-exemption design.
5. **Anomalies (surface only — NEVER propose changes)** — scan the full observations file for: unexpected actor types, denial streaks (same session denied 3+ times), gaps where expected hook fires are absent, or observation shapes not seen before. Report what you see, plainly; whether it means anything is the owner's call.
6. **Delegations & model pins** — if `state/observations.jsonl` has records with `"hook":"delegation"`: report RAW COUNTS ONLY — no compliance score, no percentages, raw counts plus one-line observations. Cover: delegations tallied by `subagent_type`/`requested_model`; the deny count; the count of records with `plan_marker_kind=="none"` AND `prompt_bytes>1500` (feature-scale-looking briefs missing a plan marker); whether a reviewer-consult (`codex_consult.sh`/`claude_consult.sh`) ran this session; and the push-consent registry state (`/haejwo:push`). Flag the count with `requested_model` null as an inheritance risk (session model leaking into execution). Then run an on-demand model-availability check for the configured `models_codex` pins (on Codex hosts: query the CLI's model list if available). An invalid/unknown pin is an anomaly — report it. A NEW model family the CLI now supports but isn't pinned is only a quiet note ("rerun `/haejwo:setup` if desired") — never a nag.
7. **Agents** — list the three tiers and reviewer slot with their models/state from config.

End with one line: gate ACTIVE/OFF, budget N, configured yes/no.
