---
description: Show haejwo's full status — config, this turn's edit counter, codex readiness, and empirical hook observations (including whether hooks fired inside subagents).
argument-hint: "(no arguments)"
---

You are the **haejwo host**. Report full plugin status. Data dir: `${CLAUDE_PLUGIN_DATA}` (if unsubstituted: `ls -d ~/.claude/plugins/data/*haejwo*`).

Gather and present compactly:
1. **Config** — `config.json` (configured?, gate on/off, budget, bash-guard, model tiers, codex enabled/verified_at, push auto-repos). If missing: say defaults are active and suggest `/haejwo:setup`.
2. **This turn** — newest file in `state/`: which code files the main agent has edited this turn (n/budget).
3. **Codex** — `codex login status` (one line).
4. **Observations** — last ~10 lines of `state/observations.jsonl`; specifically report whether any record has a non-null `agent_type` (= hooks DO fire inside subagents and exemption is active) or all null (= hooks fire only for the main agent). This is the live evidence for the gate's subagent-exemption design.
5. **Agents** — list the three tiers and codex slot with their models from config.

End with one line: gate ACTIVE/OFF, budget N, configured yes/no.
