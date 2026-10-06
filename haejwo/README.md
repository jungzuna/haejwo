# haejwo (해줘) — the cold-start plugin

> **"just handle it."** — you talk; the models work it out among themselves.

haejwo makes **multiple models run well on top of Claude Code and Codex, automatically**. You say what you want (however roughly — that's the 해줘); the host plans with an independent reviewer, delegates across cost tiers, reviews, and verifies. The expensive main model stays on **judgment** (plan, delegate, decide, synthesize), **execution** goes to cost-appropriate tiers, and a PreToolUse hook **physically blocks** the main agent when it starts implementing instead of delegating.

## The 4 layers

| Layer | Artifact | What it does |
|---|---|---|
| Declaration | SessionStart hook (`session_brief.py`) | Injects the orchestration rules every session, plus a setup nudge until setup runs and the live config summary after; a minimal core only if the rules file is unreadable or over budget |
| Roles | `agents/` | `deep-reasoner` (session model and effort) · `default-worker` (opus, high effort) · `task-worker` (opus, low effort) + the reviewer slot (`scripts/codex_consult.sh` on Claude, `scripts/claude_consult.sh` on Codex) |
| Criteria | `rules/orchestration.md` | When the main agent handles directly vs must delegate |
| **Enforcement** | PreToolUse hooks (`gate.py`, `bash_guard.py`, `delegation_gate.py`) | Main agent: max **N distinct code files per turn** (default 2) — the N+1th edit is **denied** with a delegation instruction; Bash writes to code files are denied (a heuristic over redirects, `tee` and in-place editors — see the limits below); delegating to a generic agent (general-purpose / Explore) without an explicit model is **denied**, and an omitted model that would miss a configured tier pin is checked |

## Gate semantics
- Counts **distinct code files** (config extension list) per user turn; re-editing is free. **Subagents are exempt** (`agent_id`/`agent_type` in the payload).
- The deny reason states the budget and exactly whom to delegate to; the last allowed edit warns that the budget is full.
- **Fail-open**: any hook error or ambiguity ⇒ allow. A delegation aid, not a security boundary: code written dynamically is invisible to the guard and forbidden by instruction only.
- Temp paths are never code: anything under `/tmp`, `/var/tmp` or `tempfile.gettempdir()` is unclassified, so a repository cloned under one of them is gated by neither the edit budget nor the Bash guard's code-path checks — except that an in-place editor fanned out through `find`/`xargs` is denied whatever its paths (its targets are invisible to the guard).

## First run
`SessionStart` nudges once: run **`/haejwo:setup`** — interactive choices for model tiers, edit budget, bash-guard and the independent reviewer; it probes the other CLI, smoke-tests the reviewer slot, and persists to `${CLAUDE_PLUGIN_DATA}/config.json` (survives updates). Before setup the safe defaults apply: gate ON, 2 files/turn, bash-guard ON. Presets: `Standard` (the default) / `Budget` (Sonnet/Haiku; Haiku ignores effort) / `Custom`. `deep-reasoner` inherits your session's model; `Standard` runs both workers on Opus (default-worker high effort, task-worker low).

On Claude Code the workers run at their agent file's model unless one is passed explicitly; the delegation gate steers an omitted override that would miss a configured pin, but does not verify which model ran.

## Zero-command by design
Normal use involves **no haejwo commands at all**:
- Feature-scale ask → the host runs **planning consensus** itself before implementing (`/haejwo:plan` is only a manual trigger).
- Implementation → delegated to the right tier; the gate enforces it when the host forgets.
- Push/deploy → host asks once; say "do it automatically from now on" and it records the grant.

The name-integrity rule: the moment users must **understand or manage the plugin** to get their work done, 해줘 stops being true.

## Commands
| Command | Role |
|---|---|
| `/haejwo:plan <topic>` | Pre-implementation consensus: reviewer critique (one round by default) → host-decided plan; feature-scale briefs embed it (`Plan:`) |
| `/haejwo:setup` | First-run (or re-run) interactive configuration + reviewer probe |
| `/haejwo:status` | Config, turn counter, reviewer readiness, this session's observations and delegations (read-only) |
| `/haejwo:gate [on\|off\|N\|bash on\|bash off]` | Emergency hatch / live tuning |
| `/haejwo:push [auto\|ask]` | Per-repo push consent — outward actions are host-owned, ask-first until granted (registry, not a gate) |

Gate fires are logged to `state/observations.jsonl`; `HAEJWO_GATE=off <cmd>` overrides one command.

**Effort:** the host is always your session's model. Reviewer effort policy lives in the injected rules; `default-worker` pins `high` and `task-worker` `low` in their agent files; deep-reasoner and generic agents inherit the session's effort.

## Install
See the [root README](../README.md) (both hosts). Codex: trust the hooks once via `/hooks` in interactive codex; commands surface as `@haejwo-*` skills (CI-only: `--dangerously-bypass-hook-trust`).

Hooks load at session start — restart after install or update. From 2.18 a runner invoked from a stale cache path forwards itself to the installed version (Claude Code only — Codex has no install registry); versions before 2.18 run as invoked.

**Dual-host parity (measured):** gate (apply_patch-aware), bash-guard, rules injection, turn reset and worker exemption work on both hosts; the reviewer inverts per host (principle 9); Codex tiers ride `spawn_agent` parameters (`models_codex`).

## Reviewer runners
`codex_consult.sh` (Claude host) and `claude_consult.sh` (Codex host); shared internals in `scripts/lib`.

**Usage:** `<runner> [--mode consult] [-o out.md] brief.md`, or `echo … | <runner> --mode consult -` (stdin brief, deleted on exit), from the project root. Without `-o` the reply is `<brief>.reply.md`, the log beside it. `consult` is the only mode; `--mode implement` (2.10), `--resume` (2.13) and `--snapshot` (2.22 — review the live working copy; pause writes during the review, or review a worktree you prepared) are removed.

**Exit codes:** `0` reply accepted · `1` a failed check (empty reply, failure event, tracing error, repository changed, detection unavailable) · `2` usage error, missing brief, invalid env value, refused artifact path, or an unverifiable non-git directory · `3` CLI or runner library missing · `4` no writable temp dir · `124` timeout; any other non-zero CLI status passes through.

**Guarantees**
- A standing non-editing REVIEWER CONTRACT is prepended to every brief; `claude_consult.sh` also disallows Edit/Write/NotebookEdit.
- Post-run change detection FAILS the run: HEAD, tracked status, per-path fingerprints, `git diff` / `--cached`, untracked files (presence for all, contents for the first 2000). Runner artifacts are excluded.
- **Artifact guard (2.21):** a reply, log, events or temp-brief path inside the worktree or its git dirs (lexically or via a symlink), or an existing hard-linked artifact, is refused with exit 2 before the first write and the paid call. Other worktrees of the same repository are not covered.
- `codex_consult.sh` reads failures from codex's JSONL events (top-level `turn.failed`/`error` only) plus an anchored `ERROR codex_core` tracing scan with no opt-out. A model rejected before execution fails with one hint; it is not retried.
- Fail-closed: a git error, unreadable snapshot or timeout fails the run (before the paid call when the BEFORE snapshot fails); unreadable files are disclosed (`some files unreadable: N`).
- Everything runs under a python3 wall clock. The model (codex: and effort) prints with its source; the log header names the plugin version and config file.

**Not guaranteed**
- **Not a security boundary:** Bash stays available to the reviewer.
- Never covered: package installs, MCP / user / global config, ignored or out-of-repo files, edit-then-restore. Under concurrent writers a change has **attribution unknown**.
- **Review coverage is what the reviewer could read.** Without repo access it sees only the brief; a scope-limited review names its scope and omissions in the brief and the acceptance report.
- A failure reported only in prose is NOT detected (measured 2026-09-21: a sandbox failure gave rc 0 and "I cannot read it…"); the host always reads the reply. `claude_consult.sh` has no event classifier.
- An unselected model is **`cli-default (identity unverified)`**; the Codex-host workflow is untested in the field.

**Knobs and config**
- Env wins over config; empty = unset. `CODEX_MODEL`, `CODEX_EFFORT` (`low`/`medium`/`high`/`xhigh`, runner default **`medium`**), `CODEX_SANDBOX` (`read-only` default / `workspace-write` / `danger-full-access`), `CODEX_TIMEOUT` (600s at every effort, `0` = unlimited); `CLAUDE_MODEL` / `CLAUDE_TIMEOUT` on the claude runner.
- Keys: `codex.model`, `codex.effort`, `codex.consult_sandbox`. The `codex` block describes the reviewer of the **host that owns the data dir**: the codex runner ignores model/effort under `/.codex/` and the claude runner reads them there; under `/.claude/` the reverse; under a custom plugin root each runner reads its own block. A stored `fallback_model` is ignored.
- Path, by ownership: the runner's own `<plugins>/data/haejwo-haejwo/config.json`, else `CLAUDE_PLUGIN_DATA` only when named `haejwo-haejwo` (a naming heuristic, not authentication), else `~/.claude|~/.codex/plugins/data/haejwo-haejwo/config.json`. A foreign value is ignored and disclosed once; a bad owner file never falls back elsewhere.

**Official `codex@openai-codex` plugin (1.0.6, measured 2026-09-21):** its rescue subagent is exempt from haejwo's gate and `/codex:review` reported completion without inspecting changes; keep its Stop gate OFF, and verify its diffs before acceptance.

## Conventions
[`PHILOSOPHY.md`](PHILOSOPHY.md) is the constitution; read it before changing anything. [`PROMPTS.md`](PROMPTS.md) is the style law for every prompt surface; deny strings are a tested contract.

## Verification
Run `python3 tests/test_hooks.py` and gate on the UNPIPED exit code. Live proof: 3 Edit calls on 3 code files in one turn ⇒ the 3rd is denied; `/haejwo:status` shows whether hooks fire inside subagents.
