---
description: Configure haejwo once (writes config) — model tiers, edit budget, bash-guard, and the optional independent reviewer.
argument-hint: "(no arguments)"
model: sonnet
---

You are the **haejwo host**. Configure the plugin once per machine/account. `${CLAUDE_PLUGIN_DATA}` and `${CLAUDE_PLUGIN_ROOT}` are substituted by the host for THIS plugin (measured on Claude Code; not on Codex). UNRESOLVED (literal `${…}`) → STOP before any write; use the fallback — data: Claude Code `~/.claude/plugins/data/haejwo-haejwo/`, Codex `~/.codex/plugins/data/haejwo-haejwo/`; root: this file's plugin directory.

**Helper.** One deterministic script owns probes, writes and verification (environment, timeouts, temp repository, nonce, persistence) — never re-implement them:
```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_probe.py" <mode> --config "${CLAUDE_PLUGIN_DATA}/config.json" --plugin-root "${CLAUDE_PLUGIN_ROOT}" [options]
```
Codex host: add `--host codex` (Claude reviewer, no sandbox). It prints ONE JSON object. Exit `0` ok · `2` usage error · `3` malformed/unwritable config (report the message; never repair it silently — hooks fail open until repaired) · `4` failed probe. Exit 2, and exit 3 before any write, leave the config untouched; once verification has started (exit 3 on its final write, or exit 4), the reviewer stays disabled, `verification_pending` set, `consult_sandbox` / `danger_full_access_consented_at` / `verify_fingerprint` removed.

## 1. Preflight (one Bash)
Run `keep-check` (it includes preflight). `keep` true → ONE AskUserQuestion: `Keep current (Recommended)` (description: the stored choices) / `Change settings`; Keep → step 4. Otherwise read `preflight`: `cli.installed`, `cli.auth`, `sandbox_readonly` and `reason`.

## 2. Ask — ONE call, at most 4 questions
Claude Code: AskUserQuestion. Codex: selection UI, else numbered choices.
1. **Preset** — deep-reasoner (never below default-worker) / default-worker / task-worker. Say `Standard` runs when setup is skipped.
   - Claude Code: `Standard (default)` session model / opus (effort high) / opus (effort low); `Budget` session model / sonnet / haiku; `Custom` per-role.
   - Codex (`models_codex`; offer the account's current lineup): `Standard (default)` host model for all three, deep-reasoner at the HOST's effort (omit `reasoning_effort`), default-worker `medium`, task-worker `low` (omit the model on `spawn_agent`); `Budget` host model / gpt-5.6-terra / gpt-5.6-luna; `Custom` per-role. Measured 2026-09-21: overrides need a fresh/partial context fork.
   - `Custom` → a follow-up in calls of at most four questions: the roles first, one each (default: stored value, else `Standard`), then — reviewer enabled — the reviewer model (`CLI default` REMOVES `codex.model`, or an id via Other) and, on Claude Code, effort `medium` / `high` / `xhigh` (Codex host: the Claude CLI default, on purpose). Claude Code accepts only Agent-tool aliases (sonnet/opus/haiku, plus any the session lists); full ids only on Codex. Otherwise stored reviewer model/effort are kept.
2. **Edit budget (files/turn)** — `2 (Recommended)` / `3` / `5` / `Gate off` (rules only).
3. **Bash-guard** — `On (Recommended)` blocks main-agent Bash writes to code files; `Off` = rules only.
4. **Independent reviewer**, by preflight:
   - auth authenticated or `unknown` (a login failure STOPs at the smoke), sandbox `ok` / `unknown` / `n/a`: `Enable (Recommended)` / `Skip`.
   - same auth, sandbox `broken`: `Enable danger-full-access (remember)` — no Recommended suffix; description verbatim: "Future Codex reviewers run with your permissions, including possible network access. The REVIEWER CONTRACT and git change detection are not a security boundary: package installs, MCP/user config changes, ignored/out-of-repo files, and edit-then-restore changes can escape detection. Briefs/repository content go to OpenAI. Consent is remembered until revoked." — / `Skip`.
   - installed, not logged in: `Skip for now (Recommended)` / `I'll log in now` (`! codex login`, or the `claude` login flow — then re-run setup).
   - not installed: `Skip (Recommended)` / an install-page pointer.
   Every reviewer-enabling description says briefs and repository content go to the other vendor's service. A re-run reuses a recorded consent only AFTER telling the user (name its date in the question), who may revoke it; `Skip` revokes.

## 3. Write + verify (one Bash)
`write` with ONLY the answered keys: `--preset standard|budget|custom` (custom: `--deep-reasoner X --default-worker X --task-worker X`), `--gate on --budget N` or `--gate off`, `--bash-guard on|off`, `--reviewer on|off`, Custom reviewer answers as `--model X` / `--model-default` / `--effort X`. Then, reviewer enabled, `verify` (never `--effort`; `write` stores it):
- preflight `ok` → `--sandbox read-only` (no question).
- preflight `unknown` → `--sandbox read-only`; exit 4 at stage `read` → retry ONCE; still failing → a second round: the `danger-full-access` option above / `Skip`.
- full access chosen → `--sandbox danger-full-access --consented`; a stored `danger_full_access_consented_at` goes as `--consented-at <ts>` instead (never stamp anew).
- Codex host → no `--sandbox` (the helper refuses it).

`broken` → no read-only attempt. Exit 4 at stage `smoke` → report the `cause` line and STOP; never ask the sandbox question after a smoke failure. Any other failure → report the stage and the reason line as given; never call it a sandbox defect. Outcomes the helper persists (both hosts also `verify_fingerprint`, which keep-check compares): Codex reviewer success → `consult_sandbox`, `verified_at`, `enabled=true` (+ consent stamp, full access only); Claude reviewer (sandbox not applicable): success → persist `verified_at=<probe unix-ts>`, `enabled=true`; DONE.

## 4. Report
Compact table of saved choices (stored pins kept; unset roles: today's defaults). Reviewer line: sandbox, consent date, verified date, `durations` from the JSON. Then: "Setup is saved for this machine; it is not needed again after plugin updates. /haejwo:status shows it." Session effort: start at the vendor's recommendation; raise it only when accepted outcomes, cost and rework justify it. Context dominates cost: the plugin README's By design covers `autoCompactWindow` and compact instructions. Claude Code: "This command ran on Sonnet; your session model returns with your next prompt (a resumed session may keep Sonnet — check /model)."
