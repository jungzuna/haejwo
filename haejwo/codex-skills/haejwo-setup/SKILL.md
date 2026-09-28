---
name: haejwo-setup
description: Configure haejwo once (writes config) — model tiers, edit budget, bash-guard, and the optional independent reviewer.
---

<!-- MIRROR of commands/setup.md for the Codex host — do not edit by hand;
     edit commands/setup.md and regenerate. Drift is canary-tested. -->


You are the **haejwo host**. Configure the plugin — walk ALL steps; runs once per account (config persists across sessions and updates). Data dir `${CLAUDE_PLUGIN_DATA}` (unsubstituted → `ls -d ~/.claude/plugins/data/*haejwo*`). Edit an existing `config.json`, never clobber it; disclose one that will not parse before writing — hooks are fail-open until it is repaired.

**Persistence, stated once for every step:** write `config.json` IMMEDIATELY at each real state transition (python3 read-modify-write; merge; preserve unknown keys). NEVER defer a write: a stale `enabled:true` plus `danger-full-access` consent from a PREVIOUS run must not survive a verification that just failed. Every failure/STOP branch first writes `codex.enabled=false` and REMOVES `consult_sandbox` / `danger_full_access_consented_at`. Tiers: `models` on Claude Code, `models_codex` on Codex; leave the other at defaults. `codex` names reviewer state on both hosts — on Codex, the claude reviewer.

## 1. Probe the reviewer CLI (before asking)
The reviewer is the OTHER model's CLI: Claude Code → `codex login status`, Codex → `claude --version` plus a login check if one exists. Distinguish installed+authenticated / installed-but-not-logged-in / not installed; missing or erroring is normal and supported (the reviewer is optional, review falls back per the Recovery rules) — never a failure, continue.

## 2. Ask (selection UI; one call, follow-ups only where a choice needs one)
Claude Code: AskUserQuestion. Codex: the selection UI, else numbered chat choices; persist the answers either way.
1. **Preset** — deep-reasoner / default-worker / task-worker. Say that `Standard` is what runs when setup is skipped; let the user choose on cost, not on a label.
   - Claude Code: `Standard (default)` session model / opus / opus (chores low effort); `Budget` session model / sonnet / haiku (haiku ignores effort); `Custom` per-role.
   - Codex (`models_codex`; names are account/version-dependent — offer the current lineup): `Standard (default)` host model for all three, deep-reasoner at the HOST's effort (omit `reasoning_effort`), default-worker `medium`, task-worker `low` (omit the model on `spawn_agent`); `Budget` host model / gpt-5.6-terra / gpt-5.6-luna; `Custom` per-role. Measured 2026-09-21: a child inherits the parent model and records the requested effort; overrides need a fresh or partial context fork.
   - `Custom` → one question per role, defaulting to that role's stored value else its `Standard`; other ids via Other.
2. **Edit budget (files/turn)** — `2 (Recommended)` / `3` / `5` / `Gate off` (rules stay, no physical block).
3. **Bash-guard** — `On (Recommended)` blocks main-agent Bash writes to code files (sed -i, >, tee...); `Off` = rules text only.
4. **Independent reviewer** — authenticated: `Enable (Recommended)` / `Skip`. Installed, NOT logged in: `Skip for now (Recommended)` / `I'll log in now` (`! codex login`, or the `claude` login flow — then re-run setup). Not installed: `Skip (Recommended)` / a pointer to the install page.
   - **Enabled → `Reviewer model`** — `CLI default` (no model passed; prints `cli-default (identity unverified)`) or an id via Other; `CLI default` REMOVES any stored `codex.model`. Claude Code also asks effort (`medium (default)` / `high` / `xhigh`) plus an optional fallback via Other → `codex.effort` / `codex.fallback_model`; no fallback REMOVES any stored `codex.fallback_model`; Codex asks the model ONLY, reading `codex.model` and nothing else.

Gate, bash-guard and tier answers persist with `"configured": true` in the SAME write — they ARE the configuration; a later reviewer failure must never lose them. Key names, never guessed — shape from `DEFAULT_CONFIG` in `${CLAUDE_PLUGIN_ROOT}/scripts/hjw_common.py`; setup writes `gate.{enabled,max_files_per_turn,bash_guard}`, `models.{deep_reasoner,default_worker,task_worker}` (Claude Code) / `models_codex.{…}` (Codex), `codex.{enabled,model,effort,fallback_model,consult_sandbox,verified_at,danger_full_access_consented_at}`, `configured`.

## 3. Reviewer enabled → verify end-to-end (consent-gated)
0. **Reviewer keys persist FIRST, before any probe** — `codex.model` (removed for `CLI default`), `codex.effort` (Claude Code), `codex.fallback_model`. Probes UNSET `CODEX_MODEL`/`CLAUDE_MODEL` (`env -u …`) so what is verified is what is stored; the smoke pins `CODEX_EFFORT=low` (connectivity only), the repo-read probe unsets it and verifies the stored effort.
1. **Self-contained smoke** — a tiny brief needing no repo access, safe in any sandbox. Claude Code host:
   ```
   printf 'MODE: consult\nReply with exactly: HAEJWO-OK\n' | env -u CODEX_MODEL CODEX_EFFORT=low CODEX_TIMEOUT=90 "${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh" --mode consult -
   ```
   Codex host: same brief to `env -u CLAUDE_MODEL "${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh" --mode consult -` (CLAUDE_TIMEOUT=90). Exit != 0, or no HAEJWO-OK in the reply → persist disabled, report why and the fix, STOP — never ask the sandbox question.
2. **Repo-read probe** — temp git repo in a scratch dir under the cwd; a random nonce in a file whose NAME is unrelated to it (never name the file after the nonce, never put the nonce in the brief). The brief carries ONLY that absolute path and "return the file's exact content".
3. **Run it.** Codex reviewer: `CODEX_SANDBOX=read-only` EXPLICITLY on attempt AND retry — never the default, or a stored `consult_sandbox` leaks in and makes the read-only claim false; the claude runner takes no sandbox argument. Success → persist `consult_sandbox="read-only"`, `verified_at=<probe unix-ts>`, `enabled=true`; DONE. Failure → retry ONCE, same explicit read-only. Still failing → classify from the runner's log (`$LOG` beside the reply); when the cause is not certain report exactly "read-only workspace probe failed; sandbox or CLI/tool failure", NEVER "sandbox defect". The claude runner has no sandbox concept or escalation → persist disabled, report, STOP; the codex reviewer continues.
4. **`danger-full-access` — CODEX reviewer only, ONLY after step 3 failed, retry included.** ONE question: allow it for future reviewer runs? Some hosts break the CLI's own sandboxing; this is the only way a reviewer reads the repo there. The wording MUST state plainly: the reviewer runs with the user's own permissions; the standing REVIEWER CONTRACT and git-snapshot change detection reduce risk but are NOT a security boundary; package installs, MCP/user config changes, ignored or out-of-repo files and an edit-then-restore sequence are NOT caught. Refusal → persist disabled (read-only demonstrably fails here, no escalation granted), report, STOP.
5. **On consent** — re-run the SAME-SHAPE nonce probe with `CODEX_SANDBOX=danger-full-access` set EXPLICITLY. Success → persist `consult_sandbox="danger-full-access"`, `danger_full_access_consented_at=<unix-ts>`, `verified_at=<probe unix-ts>`, `enabled=true`. Failure → persist disabled, report, STOP.
6. **Re-run / revoke** — a re-run reuses a recorded consent only AFTER telling the user it exists, so they can revoke it; disabling the reviewer removes both keys too.

## 4. Report
A compact table of the saved choices. Upgrading from ≤2.11: stored pins are preserved, a never-stored worker role (default-worker/task-worker on Claude Code) defaults to Opus, `inherit` follows the agent file (now Opus), and deep-reasoner plus every Codex role inherits. The gate reads config live; tiers apply as a `model` override on Agent-tool calls; re-run `/haejwo:setup` anytime.
