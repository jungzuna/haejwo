# haejwo (해줘) — the cold-start plugin

> **"just handle it."** — you talk; the models work it out among themselves.

haejwo makes **multiple models run well on top of Claude Code and Codex, automatically**. You say what you want (however roughly — that's the 해줘); the host plans, delegates across tiers, reviews, and verifies. The main model stays on **judgment** (plan, delegate, decide, synthesize), **execution** goes to worker tiers, and a PreToolUse hook **denies** the main agent's code edits past a per-turn budget. By default the reviewer is OFF until setup verifies it, every Claude Code worker tier is Opus, and Codex workers inherit the host model; cost gains come from cheaper pins you set after measuring.

## The 4 layers

| Layer | Artifact | What it does |
|---|---|---|
| Declaration | SessionStart hook (`session_brief.py`) | Injects the orchestration rules every session, plus a setup nudge until setup runs and the live config summary after; a minimal core only if the rules file is unreadable or over budget |
| Roles | `agents/` | `deep-reasoner` (session model and effort) · `default-worker` (opus, high effort) · `task-worker` (opus, low effort) + the reviewer slot (`scripts/codex_consult.sh` on Claude, `scripts/claude_consult.sh` on Codex) |
| Criteria | `rules/orchestration.md` | When the main agent handles directly vs must delegate |
| **Enforcement** | PreToolUse hooks (`gate.py`, `bash_guard.py`, `delegation_gate.py`) | Main agent: max **N distinct code files per turn** (default 2) — the N+1th edit is **denied**; Bash writes to code files are denied (heuristic); delegating to a generic agent (general-purpose / Explore) without an explicit model is **denied** |

## Gate semantics
- Counts **distinct code files** (config extension list) per user turn; re-editing is free. **Subagents are exempt** (`agent_id`/`agent_type` in the payload).
- The deny reason states the budget and exactly whom to delegate to; the last allowed edit warns that the budget is full.
- **Fail-open**: any hook error or ambiguity ⇒ allow. A delegation aid, not a security boundary: code written dynamically is invisible to the guard and forbidden by instruction only.
- Temp-dir paths (`/tmp`, `/var/tmp`, `tempfile.gettempdir()`) outside the active project (git toplevel, else cwd) are not code, so the scratchpad spends no budget; a temp-dir repository that IS the project is gated unless its root is a temp dir or an ancestor of one (`/tmp`, `/`). In-place editors fanned out through `find`/`xargs` are denied whatever their paths.
- By design the bash guard tries redirects, `tee` and in-place editors only — not `cp`, `mv`, `git apply` or `patch`: each added pattern costs false positives (2.20). The extension list IS the plugin's definition of code; json, yaml, html, css and tf are not on it.

## First run
**`/haejwo:setup`** is optional; a nudge repeats each session until configured. It sets model tiers, edit budget, bash-guard and the reviewer (CLI probed, slot smoke-tested); persists to `${CLAUDE_PLUGIN_DATA}/config.json` (survives updates). Before setup the safe defaults apply unless a `/haejwo:gate` value was stored: gate ON, 2 files/turn, bash-guard ON. Presets: `Standard` (default) / `Budget` (Sonnet/Haiku) / `Custom`.

On Claude Code the workers run at their agent file's model unless one is passed explicitly; the delegation gate steers an omitted override that would miss a configured pin, but does not verify which model ran.

## Zero-command by design
Normal use involves **no haejwo commands at all**:
- Feature-scale ask → the host runs **planning consensus** itself before implementing (`/haejwo:plan` is only a manual trigger).
- Implementation → delegated to a worker; generic agents must carry an explicit model (gate-enforced), and the TIER choice is the host's judgment.
- Push/deploy → host asks first; an authorization you already gave for the action counts.

The name-integrity rule: the moment users must **understand or manage the plugin** to get their work done, 해줘 stops being true.

## Commands
| Command | Role |
|---|---|
| `/haejwo:plan <topic>` | Pre-implementation consensus: reviewer critique (one round by default) → host-decided plan; feature-scale briefs embed it (`Plan:`) |
| `/haejwo:setup` | First-run (or re-run) interactive configuration + reviewer probe |
| `/haejwo:status` | Config, turn counter, reviewer readiness, this session's observations and delegations (read-only) |
| `/haejwo:gate [on\|off\|N\|bash on\|bash off]` | Emergency hatch / live tuning |

Gate fires are logged to `state/observations.jsonl`. `HAEJWO_GATE=off` works only in the host process's environment (no per-command bypass); `/haejwo:gate off` is the hatch.

**Effort:** the host uses your session effort (start from the vendor's recommendation; raise it only when accepted outcomes, cost and rework justify it). Reviewer effort policy lives in the injected rules; deep-reasoner and generic agents inherit the session's effort.

## Install
See the [root README](../README.md) (both hosts; Codex CI-only: `--dangerously-bypass-hook-trust`).

Hooks load at session start — restart after install or update. A runner invoked from a stale cache path forwards itself to the installed version (Claude Code only; pre-2.18 runners run as invoked).

**Support floor:** runners support installs >= 2.22.0; a 2.26+ runner never forwards below it (says so, runs locally); older source runners do not enforce it. Pre-floor compatibility code (the `snapshot.py` tombstone, for hops from 2.18–2.21) is deleted when the floor moves.

**Dual-host parity (measured):** gate (apply_patch-aware), bash-guard, rules injection, turn reset and worker exemption work on both hosts; the delegation gate is Claude Code-only (Codex `spawn_agent` is not hooked); the reviewer inverts per host (principle 9); Codex tiers ride `spawn_agent` parameters (`models_codex`).

## Reviewer runners
Shared internals live in `scripts/lib`; implementation detail lives in the scripts' comments.

**Usage:** `<runner> [--mode consult] [-o out.md] brief.md`, or `echo … | <runner> --mode consult -` (stdin brief, deleted on exit), from the project root. Without `-o` the reply is `<brief>.reply.md`, the log beside it. `consult` is the only mode; for a stable tree, pause writes or review a prepared worktree.

**Exit codes:** `0` reply accepted · `1` a failed check (empty reply, failure event, tracing error, repository changed, detection unavailable) · `2` usage error, missing brief, invalid env value, refused artifact path, or an unverifiable non-git directory · `3` CLI or runner library missing · `4` no writable temp dir · `124` timeout; any other non-zero CLI status passes through.

**Guarantees**
- A standing non-editing REVIEWER CONTRACT is prepended to every brief; `claude_consult.sh` also disallows Edit/Write/NotebookEdit.
- Post-run change detection FAILS the run when the repository changed: HEAD, tracked files, untracked files (contents for the first 2000). Runner artifacts are excluded.
- **Artifact guard:** a reply, log, events or temp-brief path inside the worktree or its git dirs (lexically or via a symlink), or an existing hard-linked artifact, is refused with exit 2 before the first write and the paid call. Other worktrees of the same repository are not covered.
- A codex model rejected before execution fails with one hint; it is not retried.
- Fail-closed: a git error, unreadable snapshot or timeout fails the run (before the paid call when the BEFORE snapshot fails); unreadable files are disclosed (`some files unreadable: N`).
- Every helper and CLI call runs under a python3 wall clock except the runner's bootstrap python, `realpath`, the `awk`/`grep` log scans, plain file copies and reading a stdin brief. Model and effort print with their source.

**Not guaranteed**
- **Not a security boundary:** Bash stays available to the reviewer.
- Never covered: package installs, MCP / user / global config, ignored or out-of-repo files, edit-then-restore. Under concurrent writers a change has **attribution unknown**.
- **Review coverage is what the reviewer could read.** Without repo access it sees only the brief; a scope-limited review names its scope and omissions in the brief and the acceptance report.
- A failure reported only in prose is NOT detected (measured 2026-09-21: a sandbox failure gave rc 0 and "I cannot read it…"); the host always reads the reply. `claude_consult.sh` has no event classifier.
- An unselected model is **`cli-default (identity unverified)`**; the Codex-host workflow is untested in the field.

**Knobs and config**
- Env wins over config; empty = unset. `CODEX_MODEL`, `CODEX_EFFORT` (`low`/`medium`/`high`/`xhigh`, runner default **`medium`**), `CODEX_SANDBOX` (`read-only` default / `workspace-write` / `danger-full-access`), `CODEX_TIMEOUT` (600s at every effort, `0` = unlimited); `CLAUDE_MODEL`, `HJW_CLAUDE_EFFORT` (env > `codex.effort`, else no flag: CLI default), `CLAUDE_TIMEOUT` on the claude runner.
- Keys: `codex.model`, `codex.effort`, `codex.consult_sandbox`; the `codex` block describes the reviewer of the host that owns the data dir. Each runner reads its own install's `config.json`; a foreign value is ignored and disclosed once.

**Official `codex@openai-codex` plugin (1.0.6, measured 2026-09-21):** its rescue subagent is exempt from haejwo's gate and `/codex:review` reported completion without inspecting changes; keep its Stop gate OFF, and verify its diffs before acceptance.

## Conventions
Before changing anything: [`PHILOSOPHY.md`](PHILOSOPHY.md) (the constitution) and [`PROMPTS.md`](PROMPTS.md) (prompt style law; deny strings are a tested contract).

## Verification
Run `python3 tests/test_hooks.py` and gate on the UNPIPED exit code. Live proof: 3 Edit calls on 3 code files in one turn ⇒ the 3rd is denied; `/haejwo:status` shows whether hooks fire inside subagents.
