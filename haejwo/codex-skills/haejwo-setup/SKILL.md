---
name: haejwo-setup
description: Configure haejwo once (writes config) — model tiers, edit budget, bash-guard, and the optional independent reviewer.
---

<!-- MIRROR of commands/setup.md for the Codex host — do not edit by hand;
     edit commands/setup.md and run `python3 tests/mirrors.py --write`.
     Drift is canary-tested. -->


You are the **haejwo host**. Configure the plugin — walk ALL steps, once per account. Data dir `${CLAUDE_PLUGIN_DATA}` (unsubstituted → `ls -d ~/.claude/plugins/data/*haejwo*`). Disclose a `config.json` that will not parse before writing — hooks are fail-open until it is repaired.

**Persistence (every step):** write `config.json` IMMEDIATELY at each state transition (python3 read-modify-write; preserve unknown keys) — a stale `enabled:true` or `danger-full-access` consent from a PREVIOUS run must not survive a verification that just failed. Every failure/STOP branch first writes `codex.enabled=false` and REMOVES `consult_sandbox` / `danger_full_access_consented_at`. `codex` holds reviewer state on both hosts (on Codex, the claude reviewer).

## 1. Probe the reviewer CLI (before asking)
The reviewer is the OTHER model's CLI: Claude Code → `codex login status`; Codex → `claude --version` plus any login check. Distinguish authenticated / not logged in / not installed; missing is normal — continue.

## 2. Ask (one call; follow-ups only where needed)
Claude Code: AskUserQuestion. Codex: the selection UI, else numbered chat choices.
1. **Preset** — deep-reasoner / default-worker / task-worker. Say `Standard` runs when setup is skipped.
   - Claude Code: `Standard (default)` session model / opus (effort high) / opus (effort low); `Budget` session model / sonnet / haiku; `Custom` per-role.
   - Codex (`models_codex`; offer the account's current lineup): `Standard (default)` host model for all three, deep-reasoner at the HOST's effort (omit `reasoning_effort`), default-worker `medium`, task-worker `low` (omit the model on `spawn_agent`); `Budget` host model / gpt-5.6-terra / gpt-5.6-luna; `Custom` per-role. Measured 2026-09-21: overrides need a fresh or partial context fork.
   - `Custom` → one question per role, defaulting to its stored value else `Standard`. Claude Code accepts only Agent-tool aliases (sonnet/opus/haiku, plus any the session lists); full ids only on Codex.
2. **Edit budget (files/turn)** — `2 (Recommended)` / `3` / `5` / `Gate off` (rules only).
3. **Bash-guard** — `On (Recommended)` blocks main-agent Bash writes to code files; `Off` = rules only.
4. **Independent reviewer** — authenticated: `Enable (Recommended)` / `Skip`. Installed, NOT logged in: `Skip for now (Recommended)` / `I'll log in now` (`! codex login`, or the `claude` login flow — then re-run setup). Not installed: `Skip (Recommended)` / a pointer to the install page. The question MUST say the brief and any repository content the reviewer reads go to the other vendor's service (possible repository-content egress).
   - **Enabled → `Reviewer model`** — `CLI default` (passes no model; REMOVES any stored `codex.model`) or an id via Other. Claude Code also asks effort (`medium (default)` / `high` / `xhigh`) → `codex.effort`; Codex asks the model only.

Gate, bash-guard and tier answers persist with `"configured": true` in ONE write; a later reviewer failure never loses them. Key names come from `DEFAULT_CONFIG` in `${CLAUDE_PLUGIN_ROOT}/scripts/hjw_common.py`, never guessed; setup writes `gate.{enabled,max_files_per_turn,bash_guard}`, `models.{deep_reasoner,default_worker,task_worker}` (Claude Code) / `models_codex.{…}` (Codex), `codex.{enabled,model,effort,consult_sandbox,verified_at,danger_full_access_consented_at}`, `configured`.

## 3. Reviewer enabled → verify end-to-end (consent-gated)
Before recording `verified_at`, confirm the log header's `config=<path>` is the file setup writes.
0. **Reviewer keys persist FIRST** — `codex.model` (removed for `CLI default`), `codex.effort` (Claude Code). Probes UNSET `CODEX_MODEL`/`CLAUDE_MODEL` (`env -u …`); the smoke pins `CODEX_EFFORT=low`, the repo-read probe runs under `env -u CODEX_EFFORT`.
1. **Self-contained smoke** — a tiny brief, no repo access. Claude Code host:
   ```
   printf 'MODE: consult\nReply with exactly: HAEJWO-OK\n' | env -u CODEX_MODEL CODEX_EFFORT=low CODEX_TIMEOUT=90 "${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh" --mode consult -
   ```
   Codex host: same brief to `env -u CLAUDE_MODEL HJW_CLAUDE_EFFORT=low "${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh" --mode consult -` (CLAUDE_TIMEOUT=90). Exit != 0 or no HAEJWO-OK → persist disabled, report why and the fix, STOP — never ask the sandbox question.
2. **Repo-read probe** — temp git repo in a scratch dir under the cwd; a random nonce in a file whose NAME is unrelated to it; the nonce never appears in the brief, which carries ONLY that absolute path and "return the file's exact content", piped on stdin.
3. **Run it.** Codex reviewer: `CODEX_SANDBOX=read-only` EXPLICITLY on attempt AND retry; the claude runner takes no sandbox argument. Success → persist `consult_sandbox="read-only"`, `verified_at=<probe unix-ts>`, `enabled=true`; DONE. Failure → retry ONCE. Still failing → when the log beside the reply does not show the cause, report exactly "read-only workspace probe failed; sandbox or CLI/tool failure", NEVER "sandbox defect". Claude reviewer (no sandbox to escalate) → persist disabled, report, STOP.
4. **`danger-full-access` — CODEX reviewer only, ONLY after step 3 failed, retry included.** ONE question: allow it for future reviewer runs? On hosts that break the CLI's sandboxing it is the only way to read the repo. The wording MUST state plainly: the reviewer runs with the user's own permissions; the standing REVIEWER CONTRACT and git-snapshot change detection reduce risk but are NOT a security boundary; package installs, MCP/user config changes, ignored or out-of-repo files and an edit-then-restore sequence are NOT caught. Refusal → persist disabled, report, STOP.
5. **On consent** — re-run the same nonce probe with `CODEX_SANDBOX=danger-full-access` EXPLICITLY. Success → persist `consult_sandbox="danger-full-access"`, `danger_full_access_consented_at=<unix-ts>`, `verified_at=<probe unix-ts>`, `enabled=true`. Failure → persist disabled, report, STOP.
6. **Re-run / revoke** — a re-run reuses a recorded consent only AFTER telling the user it exists, so they can revoke it; disabling the reviewer removes both keys.

## 4. Report
A compact table of the saved choices (stored pins kept; unset roles take today's defaults). The host uses your session effort; start from your model generation's vendor recommendation and compare accepted outcomes, cost and rework before raising it.
