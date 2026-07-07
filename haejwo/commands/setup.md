---
description: First-run (or re-run) configuration — pick model tiers, edit budget, bash-guard, and the optional codex reviewer via interactive choices; probes codex readiness; persists once so it never asks again.
argument-hint: "(no arguments)"
---

You are the **haejwo host**. Configure the plugin — walk through ALL steps; this runs once per account (config persists across sessions and plugin updates).

## 1. Locate config + current state
Data dir: `${CLAUDE_PLUGIN_DATA}` (if that string appears unsubstituted, resolve it: `ls -d ~/.claude/plugins/data/*haejwo*`). Read `config.json` there if it exists — you are editing, not clobbering.

## 2. Probe the reviewer CLI (before asking)
The independent reviewer is the OTHER model's CLI: on a Claude Code host probe `codex login status`; on a Codex host probe `claude --version` (then a login check if it exists). Distinguish three states: installed+authenticated / installed-but-not-logged-in / not installed. Missing or erroring is a normal, supported state (the reviewer is optional; review falls back per the Recovery rules) — never treat it as a failure; continue.

## 3. Ask the user (ONE selection-UI call, 4 questions)
Claude Code: AskUserQuestion. Codex: use the selection UI when available; if the UI tool is unavailable in the current mode, ask the same questions as numbered chat choices and persist the selected values normally.
1. **Model tiers** — host-aware presets (user can type custom via Other):
   - Claude Code host: `Standard (Recommended)` deep-reasoner=opus, default-worker=sonnet, task-worker=haiku / `Balanced` sonnet, sonnet, haiku / `Budget` sonnet, haiku, haiku.
   - Codex host (stored in `models_codex`; exact names are account/version-dependent — offer the current lineup): `Standard (Recommended)` deep-reasoner=inherit host model, default-worker=gpt-5.4, task-worker=gpt-5.4-mini / `Ultra-fast chores` same but task-worker=gpt-5.3-codex-spark / `Single-model` all inherit.
2. **Edit budget (files/turn)** — options: `2 (Recommended)` the standard default / `3` looser / `5` loose / `Gate off` rules stay, no physical block.
3. **Bash-guard** — options: `On (Recommended)` block main-agent Bash writes to code files (sed -i, >, tee...) / `Off` rules text only.
4. **Independent reviewer (the other model's CLI)** — if authenticated: `Enable (Recommended)` / `Skip`. If installed but NOT logged in: `Skip for now (Recommended)` / `I'll log in now` (Claude Code host: `! codex login`; Codex host: `claude` login flow — then re-run setup). If not installed: `Skip (Recommended)` / brief pointer to the CLI's install page.

## 4. If the reviewer is enabled: verify end-to-end ONCE
Run a tiny read-only smoke via the bundled runner for THIS host's reviewer (self-contained brief, no repo access needed — safe in any sandbox). Claude Code host:
```
printf 'MODE: consult\nReply with exactly: HAEJWO-OK\n' | CODEX_EFFORT=low CODEX_TIMEOUT=90 "${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh" --mode consult -
```
Codex host: same brief piped to `"${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh" --mode consult -` (CLAUDE_TIMEOUT=90).
Success (exit 0 + reply contains HAEJWO-OK) → reviewer verified. Failure → set the reviewer disabled, report why, and how to fix. Never use write-capable sandbox flags here — the reviewer slot is read-only by design, so this stays safe and needs no further permissions ever.

## 5. Persist
Write the merged config with python3 (heredoc) to `<data-dir>/config.json`:
`{"version":1, "configured":true, "gate":{"enabled":..., "max_files_per_turn":..., "bash_guard":...}, "models":{"deep_reasoner":...,"default_worker":...,"task_worker":...}, "models_codex":{"deep_reasoner":...,"default_worker":...,"task_worker":...}, "codex":{"enabled":..., "verified_at":<unix-ts-or-null>}}`
On a Codex host the tier answers go into `models_codex` (leave `models` at defaults); on a Claude Code host the reverse. `codex` is the legacy key name for the independent-reviewer state on BOTH hosts (on a Codex host it records the claude reviewer). Preserve unknown keys from an existing config.

## 6. Report
Show a compact summary table of the saved choices. Note: the gate reads config live (effective immediately); model tiers are applied by passing a `model` override on Agent-tool calls when they differ from agent defaults; re-run `/haejwo:setup` anytime to change.
