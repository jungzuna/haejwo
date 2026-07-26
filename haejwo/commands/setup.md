---
description: Configure haejwo once (writes config) — model tiers, edit budget, bash-guard, and the optional independent reviewer.
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
   - Claude Code host: `Standard (Recommended)` deep-reasoner=inherit (session model), default-worker=sonnet, task-worker=haiku / `Balanced` sonnet, sonnet, haiku / `Budget` sonnet, haiku, haiku.
   - Codex host (stored in `models_codex`; exact names are account/version-dependent — offer the current lineup): `Standard (Recommended)` deep-reasoner=inherit host model, default-worker=gpt-5.6-terra, task-worker=gpt-5.6-luna / `Ultra-fast chores` same but task-worker=gpt-5.3-codex-spark / `Single-model` all inherit.
2. **Edit budget (files/turn)** — options: `2 (Recommended)` the standard default / `3` looser / `5` loose / `Gate off` rules stay, no physical block.
3. **Bash-guard** — options: `On (Recommended)` block main-agent Bash writes to code files (sed -i, >, tee...) / `Off` rules text only.
4. **Independent reviewer (the other model's CLI)** — if authenticated: `Enable (Recommended)` / `Skip`. If installed but NOT logged in: `Skip for now (Recommended)` / `I'll log in now` (Claude Code host: `! codex login`; Codex host: `claude` login flow — then re-run setup). If not installed: `Skip (Recommended)` / brief pointer to the CLI's install page.

## 4. If the reviewer is enabled: verify end-to-end (consent-gated)
Every failure/STOP branch below writes its `codex.*` state to `<data-dir>/config.json` IMMEDIATELY, right there, before stopping — never deferred to step 5. This matters because step 5 may never run (the flow stops here): a config left over from a PREVIOUS run (e.g. a stale `enabled:true` + `danger-full-access` consent) must not survive a verification that just failed. Each such write: merge into the existing config, set `codex.enabled=false`, and REMOVE `consult_sandbox`/`danger_full_access_consented_at` if present — same python3 heredoc pattern as step 5, just scoped to the `codex` key and run right away.
1. **Self-contained smoke** (unchanged) — run a tiny self-contained brief through the bundled runner for THIS host's reviewer (no repo access needed — safe in any sandbox). Claude Code host:
   ```
   printf 'MODE: consult\nReply with exactly: HAEJWO-OK\n' | CODEX_EFFORT=low CODEX_TIMEOUT=90 "${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh" --mode consult -
   ```
   Codex host: same brief piped to `"${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh" --mode consult -` (CLAUDE_TIMEOUT=90).
   Fails (exit != 0 or reply doesn't contain HAEJWO-OK) → persist disabled state NOW (per the rule above), report why and how to fix, STOP — never ask the sandbox question.
2. **Repo-read probe** — create a temp git repo in a scratch dir under the current working directory, and write a random nonce into a file whose NAME is unrelated to the nonce (never name the file after the nonce, and never put the nonce itself in the brief). The brief given to the runner contains ONLY the file's absolute path plus the instruction to return the file's exact content — nothing else.
3. **Run the probe** via the runner with its read-only default (no sandbox override). Success → persist `codex.consult_sandbox="read-only"`, `codex.verified_at=<probe unix-ts>`, `codex.enabled=true` — DONE; never ask the sandbox question. On failure, retry ONCE. If it still fails, classify from the runner's log (`$LOG` next to the reply) — if the cause isn't certain, report exactly: "read-only workspace probe failed; sandbox or CLI/tool failure" — NEVER assert "sandbox defect" (you don't have enough signal to know which). The claude runner has no sandbox concept and no escalation path — on this failure, persist disabled state NOW, report, and STOP. For the Codex reviewer, continue to step 4.
4. **`danger-full-access` question — CODEX reviewer only, and ONLY after step 3 failed (retry included).** Ask the user (selection UI) whether to allow `danger-full-access` for future reviewer runs — some hosts break the CLI's own sandboxing, and this escalation is the only way a reviewer can read the repo there. Consent wording MUST state, plainly: the reviewer runs with the user's own permissions; the standing REVIEWER CONTRACT and git-snapshot change detection reduce risk but are NOT a security boundary; package installs, MCP/user config changes, ignored or out-of-repo files, and an edit-then-restore sequence are NOT caught by post-run detection. Refusal → persist disabled state NOW (read-only demonstrably fails on this host and no escalation was granted), report, and STOP.
5. **On consent** — re-run the SAME-SHAPE nonce probe under `CODEX_SANDBOX=danger-full-access`. Success → persist the escalation NOW: `codex.consult_sandbox="danger-full-access"`, `codex.danger_full_access_consented_at=<unix-ts>`, `codex.verified_at=<probe unix-ts>`, `codex.enabled=true`. Re-probe failure → persist disabled state NOW, report, and STOP.
6. **Re-run / revoke** — re-running `/haejwo:setup` re-uses a previously recorded `danger-full-access` consent only AFTER telling the user it exists (so they can revoke it). Disabling the reviewer REMOVES both `consult_sandbox` and `danger_full_access_consented_at` from the stored config, written immediately per the rule above.

## 5. Persist
Write the merged config with python3 (heredoc) to `<data-dir>/config.json`:
`{"version":1, "configured":true, "gate":{"enabled":..., "max_files_per_turn":..., "bash_guard":...}, "models":{"deep_reasoner":...,"default_worker":...,"task_worker":...}, "models_codex":{"deep_reasoner":...,"default_worker":...,"task_worker":...}, "codex":{"enabled":..., "verified_at":<unix-ts-or-null>, "consult_sandbox":<"read-only"|"danger-full-access"-or-absent>, "danger_full_access_consented_at":<unix-ts-or-absent>}}`
On a Codex host the tier answers go into `models_codex` (leave `models` at defaults); on a Claude Code host the reverse. `codex` is the legacy key name for the independent-reviewer state on BOTH hosts (on a Codex host it records the claude reviewer). This step only runs when step 4 didn't already STOP; the `codex` block here just reconfirms what step 4 already persisted immediately (its own success or failure/STOP branch) — `consult_sandbox`/`danger_full_access_consented_at` present only per step 4.3 (read-only) or step 4.5 (danger-full-access), absent otherwise. Preserve unknown keys from an existing config.

## 6. Report
Show a compact summary table of the saved choices. Note: the gate reads config live (effective immediately); model tiers are applied by passing a `model` override on Agent-tool calls when they differ from agent defaults; re-run `/haejwo:setup` anytime to change.
