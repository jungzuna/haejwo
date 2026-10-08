# haejwo (해줘) — the cold-start plugin

haejwo keeps the main model on **judgment** and sends **execution** to worker tiers on Claude Code and Codex. Defaults: Claude Code worker tiers are Opus, Codex workers inherit the host model, the reviewer is OFF until setup verifies it.

## The 4 layers

| Layer | Artifact | What it does |
|---|---|---|
| Declaration | SessionStart hook (`session_brief.py`) | Injects the rules, a setup nudge until configured, and the config summary; a minimal core if the rules are unreadable or over budget |
| Roles | `agents/` | `deep-reasoner` (session model and effort) · `default-worker` (opus, high effort) · `task-worker` (opus, low effort) + the reviewer slot (`scripts/codex_consult.sh` on Claude, `scripts/claude_consult.sh` on Codex) |
| Criteria | `rules/orchestration.md` | When the main agent handles directly vs must delegate |
| **Enforcement** | PreToolUse hooks (`gate.py`, `bash_guard.py`, `delegation_gate.py`) | Main agent: edits past **N distinct code files per turn** (default 2), Bash writes to code (heuristic) and generic-agent (general-purpose / Explore) delegation without an explicit model are **denied** |

## Gate semantics
- Counts **distinct code files** (config extension list) per user turn; re-editing is free. **Subagents are exempt** (`agent_id`/`agent_type` in the payload).
- Turn state is per session id, sanitized and cut to 80 chars: ids alike in those chars, or absent (`unknown`), share a counter.
- Denials name the budget and whom to delegate to; the last allowed edit warns the budget is full.
- **Fail-open**: any hook error or ambiguity ⇒ no objection. haejwo never approves a tool call, only denies or adds a note, so your permission settings still apply. An affordance, not a security boundary (P4): editing `config.json` (not code) turns the gate off; dynamically written code is invisible to the guard.
- Temp-dir paths (`/tmp`, `/var/tmp`, `tempfile.gettempdir()`) outside the active project (git toplevel, else cwd) are not code; a temp-dir repository that IS the project is gated unless its root is a temp dir or ancestor (`/tmp`, `/`). In-place editors fanned out through `find`/`xargs` are denied whatever their paths.

## First run
**`/haejwo:setup`** is optional; a nudge repeats each session until configured. It sets tiers, budget, bash-guard and the reviewer (probed, smoke-tested) in `${CLAUDE_PLUGIN_DATA}/config.json`. Before setup the defaults apply unless a `/haejwo:gate` value was stored: gate ON, 2 files/turn, bash-guard ON. Presets: `Standard` / `Budget` (Sonnet/Haiku) / `Custom`.

On Claude Code a worker runs at its agent file's model unless one is passed; the delegation gate steers an omitted override that would miss a configured pin but never verifies which model ran.

## Commands
Normal use needs **none**: the host plans, delegates and asks before push/deploy itself (an authorization you already gave counts).

| Command | Role |
|---|---|
| `/haejwo:plan <topic>` | Pre-implementation consensus: reviewer critique → host-decided plan; feature-scale briefs embed it (`Plan:`) |
| `/haejwo:setup` | Interactive configuration + reviewer probe |
| `/haejwo:status` | Config, turn counter, reviewer readiness, this session's observations and delegations (read-only) |
| `/haejwo:gate [on\|off\|N\|bash on\|bash off]` | Emergency hatch / live tuning |

Gate fires are logged to `state/observations.jsonl`. `HAEJWO_GATE=off` works only in the host process's environment (no per-command bypass); `/haejwo:gate off` is the hatch.

## Install
See the [root README](../README.md); Codex in headless CI only: `--dangerously-bypass-hook-trust`.

`/reload-plugins` (Claude Code) refreshes hooks, commands and agents in an open session; the injected rules re-load only at session start — restart after install or update.

**Support floor:** runners support installs >= 2.22.0; a 2.26+ runner never forwards below it (says so, runs locally). Pre-floor installs are unsupported as sources too: the `snapshot.py` tombstone is gone, so a pre-floor runner hopping here stays local on its own version.

**Dual-host parity (measured):** all but the delegation gate (Codex `spawn_agent` is not hooked) works on both hosts, the gate apply_patch-aware; the reviewer inverts per host; Codex tiers ride `spawn_agent` parameters (`models_codex`).

## Reviewer runners
**Usage:** `<runner> [--mode consult] [-o out.md] brief.md`, or `echo … | <runner> --mode consult -` (stdin brief, deleted on exit), from the project root. Without `-o` the reply is `<brief>.reply.md`, the log beside it. For a stable tree, pause writes or review a prepared worktree.

**Exit codes:** `0` accepted · `1` failed check (empty reply, failure event, tracing error, repository changed, detection unavailable) · `2` usage error, missing brief, bad env value, refused artifact path, non-git directory · `3` CLI or library missing · `4` no writable temp dir · `124` timeout; other statuses pass through.

**Guarantees**
- A standing non-editing REVIEWER CONTRACT is prepended to every brief; `claude_consult.sh` also disallows Edit/Write/NotebookEdit.
- Post-run change detection FAILS the run when the repository changed: HEAD, tracked files, untracked files (contents for the first 2000). Runner artifacts are excluded.
- **Artifact guard:** a reply, log, events or temp-brief path inside the worktree or its git dirs (lexically or via a symlink), or an existing hard-linked artifact, is refused (exit 2) before the first write and the paid call. Other worktrees of the same repository are not covered.
- A codex model rejected before execution fails with one hint; it is not retried.
- Fail-closed: a git error, unreadable snapshot or timeout fails the run (before the paid call when the BEFORE snapshot fails); unreadable files are disclosed (`some files unreadable: N`).
- Python helpers and the reviewer CLI run under a python3 wall clock; the runner's bootstrap python, coreutils calls, log scans, file copies and reading a stdin brief do not. Model and effort print with their source.

**Not guaranteed**
- **Not a security boundary:** Bash stays available to the reviewer.
- Never covered: package installs, MCP / user / global config, ignored or out-of-repo files, edit-then-restore. Under concurrent writers a change has **attribution unknown**.
- **Review coverage is what the reviewer could read.** Without repo access it sees only the brief; a scope-limited review names its scope and omissions.
- A failure reported only in prose is NOT detected (measured 2026-09-21); the host always reads the reply. `claude_consult.sh` has no event classifier.
- An unselected model is **`cli-default (identity unverified)`**; the Codex-host workflow is untested in the field.
- The `codex` block names the reviewer of the host that owns the data dir — on a Codex host `codex.model` is a Claude model; a rename is deferred as schema debt.

**Knobs and config**
- Env wins over config; empty = unset. `CODEX_MODEL`, `CODEX_EFFORT` (`low`/`medium`/`high`/`xhigh`, runner default **`medium`**), `CODEX_SANDBOX` (`read-only` default / `workspace-write` / `danger-full-access`), `CODEX_TIMEOUT` (600s at every effort, `0` = unlimited); `CLAUDE_MODEL`, `HJW_CLAUDE_EFFORT` (env > `codex.effort`, else no flag: CLI default), `CLAUDE_TIMEOUT` on the claude runner.
- Keys: `codex.model`, `codex.effort`, `codex.consult_sandbox`. Each runner reads its own install's `config.json`; a foreign value is ignored and disclosed once.

**Official `codex@openai-codex` plugin (1.0.6, measured 2026-09-21):** its rescue subagent is exempt from haejwo's gate and `/codex:review` reported completion without inspecting changes; keep its Stop gate OFF and verify its diffs.

## By design
Written rejections of recurring objections:
- The host's budget applies despite same-model workers: it limits host-context growth, not model price.
- The budget counts allowed attempts, not successful edits: one hook.
- Runners do not check `enabled`/`verified_at`: the host is the policy gate, the runner a tool.
- A stored `danger-full-access` consent persists; every run header discloses it. The Claude reviewer keeps Bash for tests and greps; detected repository changes fail the run (documented exclusions apply).
- Stale cache-path runners forward to the installed version (Claude Code only): restarting would push a measured host failure onto users; the support floor bounds the cost.
- The runner stack keeps the artifact guard and change detection: plain status/diff checks omit their contracts.
- The bash guard covers redirects, `tee` and in-place editors only — no `cp`/`mv`/`git apply`/`patch` heuristics, no longer extension list (json, yaml, html, css, tf are not code): each costs false positives (2.20). Known gaps: a quoted redirect target passes; `tee` checks its first target only.
- Explore runs on the `opus` alias under a Fable host, else inherits the session model (Claude Code docs): the explicit-model rule adds no cost.
- A session with no delegation capability (no Agent tool / `spawn_agent`) is outside the gate's design and undetectable by it — stop and resume on the next user turn, or use the emergency override.
- Plan-marker counts and anomaly detection are field observation the owner reads (P8).
- The host runs at your session effort: start from your model generation's vendor recommendation; raise it only when accepted outcomes, cost and rework justify it.
- Tests pinning documentation sentences are how four cold audits became fixes (P7).

## Conventions
Maintainers read [`PHILOSOPHY.md`](PHILOSOPHY.md) and [`PROMPTS.md`](PROMPTS.md) first; deny strings are a tested contract.

## Verification
Run `python3 tests/test_hooks.py` and gate on the UNPIPED exit code. Live proof: 3 Edit calls on 3 code files in one turn ⇒ the 3rd is denied; `/haejwo:status` shows whether hooks fire inside subagents.
