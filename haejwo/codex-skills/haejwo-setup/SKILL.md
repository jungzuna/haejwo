---
name: haejwo-setup
description: Configure haejwo once (writes config) — model tiers, edit budget, bash-guard, and the optional independent reviewer.
---

<!-- MIRROR of commands/setup.md for the Codex host — do not edit by hand;
     edit commands/setup.md and run `python3 tests/mirrors.py --write`.
     Drift is canary-tested. -->


You are the **haejwo host**. Configure the plugin: walk ALL steps, once per account. `${CLAUDE_PLUGIN_DATA}` and `${CLAUDE_PLUGIN_ROOT}` are substituted by the host for THIS plugin (measured on Claude Code; not on Codex). UNRESOLVED (literal `${…}`) → STOP before any write; use the fallback — data: Claude Code `~/.claude/plugins/data/haejwo-haejwo/`, Codex `~/.codex/plugins/data/haejwo-haejwo/`; root: this file's plugin directory. Disclose an unparseable `config.json` before writing (hooks fail open until repaired).

**Persistence (every step):** write `config.json` IMMEDIATELY at each state transition (python3 read-modify-write; preserve unknown keys) — no earlier `enabled:true` or consent survives a failed verification. Every failure/STOP branch first writes `codex.enabled=false` and REMOVES `consult_sandbox` / `danger_full_access_consented_at`. `codex` holds reviewer state on both hosts (on Codex, the claude reviewer).

## 1. Probe the reviewer CLI (before asking)
The OTHER model's CLI: Claude Code → `codex login status`; Codex → `claude --version` plus any login check. Distinguish authenticated / not logged in / not installed; missing → continue.

## 2. Ask (one call; follow-ups only where needed)
Claude Code: AskUserQuestion. Codex: selection UI, else numbered choices.
1. **Preset** — deep-reasoner (never below default-worker) / default-worker / task-worker. Say `Standard` runs when setup is skipped.
   - Claude Code: `Standard (default)` session model / opus (effort high) / opus (effort low); `Budget` session model / sonnet / haiku; `Custom` per-role.
   - Codex (`models_codex`; offer the account's current lineup): `Standard (default)` host model for all three, deep-reasoner at the HOST's effort (omit `reasoning_effort`), default-worker `medium`, task-worker `low` (omit the model on `spawn_agent`); `Budget` host model / gpt-5.6-terra / gpt-5.6-luna; `Custom` per-role. Measured 2026-09-21: overrides need a fresh/partial context fork.
   - `Custom` → one question per role (default: stored value, else `Standard`). Claude Code accepts only Agent-tool aliases (sonnet/opus/haiku, plus any the session lists); full ids only on Codex.
2. **Edit budget (files/turn)** — `2 (Recommended)` / `3` / `5` / `Gate off` (rules only).
3. **Bash-guard** — `On (Recommended)` blocks main-agent Bash writes to code files; `Off` = rules only.
4. **Independent reviewer** — authenticated: `Enable (Recommended)` / `Skip`. Installed, NOT logged in: `Skip for now (Recommended)` / `I'll log in now` (`! codex login`, or the `claude` login flow — then re-run setup). Not installed: `Skip (Recommended)` / an install-page pointer. The question MUST say the brief and any repository content the reviewer reads go to the other vendor's service.
   - **Enabled → `Reviewer model`** — `CLI default` (passes no model; REMOVES any stored `codex.model`) or an id via Other. Claude Code also asks effort (`medium (default)` / `high` / `xhigh`) → `codex.effort`; Codex: model only.

Gate, bash-guard and tier answers persist with `"configured": true` in ONE write; a later reviewer failure never loses them. Key names come from `DEFAULT_CONFIG` in `${CLAUDE_PLUGIN_ROOT}/scripts/hjw_common.py`, never guessed; setup writes `gate.{enabled,max_files_per_turn,bash_guard}`, `models.{deep_reasoner,default_worker,task_worker}` (Claude Code) / `models_codex.{…}` (Codex), `codex.{enabled,model,effort,consult_sandbox,verified_at,danger_full_access_consented_at}`, `configured`.

## 3. Reviewer enabled → verify end-to-end (consent-gated)
Before recording `verified_at`, confirm the log header's `config=<path>` is setup's file.
0. **Reviewer keys persist FIRST** — `codex.model` (removed for `CLI default`), `codex.effort` (Claude Code). Probes UNSET `CODEX_MODEL`/`CLAUDE_MODEL` (`env -u …`); the smoke pins `CODEX_EFFORT=low`, the repo-read probe runs under `env -u CODEX_EFFORT`.
1. **Self-contained smoke** — a tiny brief, no repo access, run inside a git repo (`claude_consult.sh` refuses a non-git cwd). Claude Code host:
   ```
   printf 'MODE: consult\nReply with exactly: HAEJWO-OK\n' | env -u CODEX_MODEL CODEX_EFFORT=low CODEX_TIMEOUT=90 "${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh" --mode consult -
   ```
   Codex host: same brief to `env -u CLAUDE_MODEL HJW_CLAUDE_EFFORT=low "${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh" --mode consult -` (CLAUDE_TIMEOUT=90). Exit != 0 or no HAEJWO-OK → persist disabled, report cause and fix, STOP — never ask the sandbox question.
2. **Repo-read probe** — temp git repo from `mktemp -d` (never under the cwd; deleted when setup ends) holding a random nonce in a file whose NAME is unrelated to it; the stdin brief carries ONLY that absolute path and "return the file's exact content", never the nonce.
3. **Run it.** Codex reviewer: `CODEX_SANDBOX=read-only` EXPLICITLY on attempt AND retry. Success → persist `consult_sandbox="read-only"`, `verified_at=<probe unix-ts>`, `enabled=true`; DONE. Failure → retry ONCE. Still failing → if the reply's log does not show the cause, report exactly "read-only workspace probe failed; sandbox or CLI/tool failure", NEVER "sandbox defect". Claude reviewer (sandbox not applicable): success → persist `verified_at=<probe unix-ts>`, `enabled=true`; DONE. Failure → persist disabled, report, STOP.
4. **`danger-full-access` — CODEX reviewer only, ONLY after step 3 failed, retry included.** ONE question: allow it for future reviewer runs (the only repo access where the CLI's sandbox is broken)? The wording MUST state: the reviewer runs with the user's own permissions; the standing REVIEWER CONTRACT and git-snapshot change detection reduce risk but are NOT a security boundary; package installs, MCP/user config changes, ignored or out-of-repo files and an edit-then-restore sequence are NOT caught. Refusal → persist disabled, report, STOP.
5. **On consent** — re-run the same nonce probe with `CODEX_SANDBOX=danger-full-access` EXPLICITLY. Success → persist `consult_sandbox="danger-full-access"`, `danger_full_access_consented_at=<unix-ts>`, `verified_at=<probe unix-ts>`, `enabled=true`. Failure → persist disabled, report, STOP.
6. **Re-run / revoke** — a re-run reuses a recorded consent only AFTER telling the user, who may revoke it; disabling the reviewer removes both keys.

## 4. Report
Compact table of saved choices (stored pins kept; unset roles: today's defaults). Session effort: start at the vendor's recommendation; raise it only when accepted outcomes, cost and rework justify it. Context dominates cost: see the plugin README's By design note on `autoCompactWindow` and compact instructions.
