# haejwo (해줘) — the cold-start plugin

> **"just handle it."** — you talk; the models work it out among themselves.

Claude Code and Codex are the official coding harnesses: complete, widely used, and best matched to their models. haejwo is the cold-start plugin — a lubricant layer that makes **multiple models run well on top of them, automatically**. You don't drive it with commands: you just say what you want (however roughly — that's the 해줘), and the host plans with an independent reviewer, delegates across cost tiers, reviews, and verifies.

Underneath, it keeps the expensive main model on **judgment** (plan, delegate, decide, synthesize) and pushes **execution** to cost-appropriate tiers — and doesn't just ask nicely: a PreToolUse hook **physically blocks** the main agent when it starts implementing instead of delegating.

## The 4 layers

| Layer | Artifact | What it does |
|---|---|---|
| Declaration | SessionStart hook (`session_brief.py`) | Injects the full orchestration rules from the first session — plus a setup nudge and a `defaults — not configured` summary until setup runs, the live config summary after; a minimal operating core only on the emergency degrade (rules file unreadable or over budget) |
| Roles | `agents/` | `deep-reasoner` (session model) · `default-worker` (opus) · `task-worker` (opus, low effort) + reviewer slot (`scripts/codex_consult.sh` on Claude, `scripts/claude_consult.sh` on Codex — outside perspective, non-editing contract with post-run change detection) |
| Criteria | `rules/orchestration.md` | When the main agent handles directly vs must delegate |
| **Enforcement** | PreToolUse hooks (`gate.py`, `bash_guard.py`) | Main agent: max **N distinct code files per turn** (default 2) — the N+1th edit is **denied** with a delegation instruction; Bash writes to code files are denied outright |

## Gate semantics
- Counts **distinct code files** per user turn (re-editing the same file is free — iteration is fine).
- Turn boundary: `UserPromptSubmit` reset + lazy `prompt_id` change detection (either alone suffices).
- **Subagents are exempt** (payload `agent_id`/`agent_type` present ⇒ allow) — delegation must never be blocked.
- Code file = extension list in config (py/ts/js/go/rs/...); docs, configs, scratch/tmp paths don't count.
- Deny reason (the model sees it verbatim): budget state + exactly whom to delegate to.
- The last allowed edit also injects a "budget now full — delegate further edits" warning.
- **Fail-open**: any hook error/ambiguity ⇒ allow. This is a delegation gate, not a security boundary.
- Known bypass gap (accepted): commands that write code dynamically are regex-invisible to the guard; the injected rules forbid them by instruction. This is a delegation aid, not a security boundary.

## First run
`SessionStart` nudges once: run **`/haejwo:setup`** — interactive choices (AskUserQuestion) for model tiers, edit budget, bash-guard, independent reviewer; probes the other CLI and smoke-tests the reviewer slot (non-editing contract with post-run change detection); persists to `${CLAUDE_PLUGIN_DATA}/config.json` (survives plugin updates — asked once, never again). Safe defaults are active even before setup: gate ON, 2 files/turn, bash-guard ON. Presets are host-specific: on Claude Code `Standard` (the default, and what runs if you skip setup) / `Budget` / `Custom` (per-role); on Codex `Standard` / `Budget` / `Ultra-fast chores` / `Custom`. The default `deep-reasoner` tier inherits your session's model — judgment is never re-pointed; `Standard` runs default-worker and task-worker on Opus (chores at low effort), and `Budget` is the cheaper opt-in (Sonnet/Haiku — Haiku ignores the effort setting).

On Claude Code, with haejwo's shipped agent definitions, `inherit` is meaningful for deep-reasoner only; default-worker and task-worker run at the agent file's default model unless an explicit model is passed. The delegation gate steers an omitted override that would miss a configured pin; it does not verify which model actually ran.

## Zero-command by design
Normal use involves **no haejwo commands at all**. You talk; the host does the rest automatically:
- Feature-scale ask → the host runs the **planning consensus** procedure itself (independent-reviewer debate → agreed plan) before implementing — `/haejwo:plan` exists only as an optional manual trigger.
- Implementation → delegated to the right tier; the gate enforces it when the host forgets.
- Push/deploy → host asks once; say "do it automatically from now on" and it records the grant.

Commands are for **settings and inspection only** (below). The name-integrity rule: the moment users must **understand or manage the plugin** — delegation, reviewer consensus, edit limits, tiers, or recovery commands — to get their work done, 해줘 stops being true. Any change that requires that is off-concept.

## Commands
| Command | Role |
|---|---|
| `/haejwo:plan <topic>` | Pre-implementation consensus: independent-reviewer debate → agreed plan (conversation-first; file only on request); feature-scale briefs embed it (`Plan:` section) |
| `/haejwo:setup` | First-run (or re-run) interactive configuration + reviewer probe |
| `/haejwo:status` | Config, this turn's counter, reviewer readiness, subagent-hook observations |
| `/haejwo:gate [on\|off\|N\|bash on\|bash off]` | Emergency hatch / live tuning |
| `/haejwo:push [auto\|ask]` | Per-repo push consent — outward actions are host-owned, ask-first until granted (registry, not a gate) |

(Cross-session/project activity stays auditable without a command: the gate hooks record every fire — actor, instance, file — in `state/observations.jsonl`; ask the host to analyze it when needed.)

Env override for a single command: `HAEJWO_GATE=off <cmd>`.

**Reasoning policy:** The orchestrating host is always your session's model (`/model` in Claude Code, the model picker in Codex); haejwo configures only worker tiers and reviewer selection. Reviewer effort scales with the decision's stakes — `medium` for routine checks, `high` (runner default) for standard consults, `xhigh` reserved for architecture forks / security-critical calls / final deadlock rounds; non-reasoning probes stay explicit `low`. Claude-host same-family fallback uses `deep-reasoner`; other-CLI review uses the configured runner. Uniform max dilutes budget exactly where judgment compounds.

## Install

See the [root README](../README.md) for install (GitHub or local-clone marketplace add, both hosts). Codex: trust the hooks once in interactive codex via `/hooks`; commands surface as `@haejwo-*` skills. (CI-only: headless pipelines may pass `--dangerously-bypass-hook-trust` — never needed, and not recommended, for interactive use.)

Hooks load at session start — restart the session (or `/reload-plugins` on Claude Code) after install.

**Dual-host parity:** gate (apply_patch-aware, whole-patch atomic deny), bash-guard (codex names its shell tool `Bash` too), rules injection, turn reset (`turn_id`), worker exemption (codex subagents carry the same `agent_type`/`agent_id` fields — measured), and the independent reviewer inverts per host: codex_consult.sh on Claude, **claude_consult.sh on Codex** (principle 9: a different model). Codex-side tiers ride the native `spawn_agent` model/effort parameters — judgment inherits the host model; execution runs at the configured tiers (`models_codex` in config).

Optional hardening (README-only, not auto-applied): add `permissions.deny` rules for `Bash(sed:*)` etc. and deny `Read` of the state dir to prevent tampering.

## Reviewer runners

`codex_consult.sh` (Claude host) and `claude_consult.sh` (Codex host) run the independent reviewer.

**Guarantees**
- The standing non-editing REVIEWER CONTRACT is prepended to every brief; `claude_consult.sh` also disables the direct edit tools (Edit/Write/NotebookEdit) via `--disallowedTools`.
- Post-run change detection FAILS the run: HEAD, tracked file status, per-path working-tree fingerprints, `git diff` / `git diff --cached`, and untracked files (presence for all, contents for the first 2000 sorted). Runner-owned artifacts are excluded from all of those, digests included.
- `codex_consult.sh` classifies failures from codex's own JSONL event stream (top-level `turn.failed`/`error` only — quoted error text inside a reply never fails the run) plus an anchored `ERROR codex_core` tracing scan.
- Fail-closed on git or snapshot errors: any git error, unreadable snapshot or timeout fails the run instead of reading as "no change" — including a git probe that cannot say whether this is a repo. A failed BEFORE snapshot fails before the reviewer is invoked, so an unverifiable run is never paid for, and unreadable files are counted and disclosed (`some files unreadable: N`) instead of silently skipped.
- Every helper and the reviewer call itself run under a python3-enforced wall clock (no dependency on the `timeout` binary).
- `codex_consult.sh` prints the selected model AND effort with their source on every run; `claude_consult.sh` prints the model with its source only (that runner has no effort knob).

**Not guaranteed**
- **Not a security boundary.** Bash stays available to the reviewer (`claude -p` has no read-only sandbox), so the contract is a norm with a backstop, not a sandbox.
- Never covered by change detection: package installs, MCP / user / global config changes, ignored files, out-of-repo files, and an edit-then-restore sequence.
- Under concurrent writers, a detected change means "something changed" — **attribution unknown**.
- A failure the reviewer reports only in its prose is NOT detected — measured 2026-09-21 on both this runner and the official `codex@openai-codex` plugin (a read-only sandbox failure produced rc 0 and "I cannot read it…" on both paths). The host reads the reply, always.
- `claude_consult.sh` has no event-stream classifier.
- An unselected model is a **`cli-default (identity unverified)`** — the runner does not know which model answered.
- The Codex-host side (`claude_consult.sh`, `spawn_agent` tiers) is less exercised in the field than the Claude Code side (spawn_agent effort inheritance measured once on 2026-09-21; the rest of the Codex-host path remains less exercised).

**Knobs**
- `codex.model` / `codex.effort` / `codex.fallback_model` in config; env (`CODEX_MODEL` / `CODEX_EFFORT` / `CLAUDE_MODEL`) wins. That `codex` block describes the reviewer of the **host that owns the data dir**, so it is read host-relative: the codex runner ignores it under a `/.codex/` config path, the claude runner reads it only there.
- `--snapshot` — review a detached worktree instead of the live working copy (below).
- `CODEX_SANDBOX` — `read-only` (default) / `workspace-write` / `danger-full-access`; the claude runner takes no sandbox argument.
- `CODEX_TIMEOUT` (`CLAUDE_TIMEOUT` on the claude runner) — seconds; `0` = unlimited.
- `CODEX_ALLOW_MARKERS=1` — disables ONLY the anchored tracing scan, nothing else.

**Snapshot (`--snapshot`, both runners)**
- A detached worktree of HEAD plus the **net** uncommitted changes (one `git diff --binary <SHA>` patch replayed with `git apply --index` — staged and unstaged states are not reproduced separately) plus untracked non-ignored files (first 2000 sorted; symlinks copied **as links**, never followed) — so you can keep editing while the reviewer works.
- **Ignored files are omitted**, so dependencies may be missing; the brief tells the reviewer to report a missing capability rather than install anything.
- Isolation, **not containment**: the snapshot shares the repository's `.git`, and change detection runs *inside* it, so writes to the **original** during the run are undetected by design.
- Capture is **not atomic**: it is timestamped, every copied file is hashed at source *and* destination, and a drift re-check refuses (`original changed during capture — retry`) rather than shipping a torn state. Every other capture failure exits 2 with `snapshot unavailable: <reason>` **before** the paid call (unborn HEAD, unresolved conflicts, gitlinks and embedded untracked repos are refused up front).
- Disclosed on every line: `snapshot=<sha7>[+dirty(<patch paths>,<untracked copied>[,partial])]`; the log adds the SNAP path, full SHA, capture interval, patch bytes, untracked copied/eligible, a `snapshot_digest` and the omissions. The worktree is removed through git (never a repo-wide `worktree prune`) before the result line, and a removal failure is reported after the reply and still exits non-zero.

- **Official `codex@openai-codex` plugin (measured 1.0.6, 2026-09-21)** — its `codex:codex-rescue` subagent is exempt from haejwo's edit budget and bash guard and carries no no-environment-change contract; rescue was blocked by that host's sandbox failure (bwrap hosts); `/codex:review` reported completion without inspecting changes, while adversarial review of an inlined diff succeeded; leave its optional Stop review gate OFF pending loop testing. When the host delegates there, the host owns the boundary: a bounded brief, no overlapping writes to the same files, and diff + oracle verification before acceptance.

## Conventions
The constitution lives in [`PHILOSOPHY.md`](PHILOSOPHY.md) — 13 principles with their origin cases, the precedence order for conflicts, and the docs map. Read it before changing ANYTHING. Prompt & style policy lives in [`PROMPTS.md`](PROMPTS.md) — every prompt surface (commands, agents, rules, hook-emitted messages, script text) follows it; deny-message strings are a tested contract.

## Verification
`scripts/` are plain python3 (stdlib only). Synthetic tests pipe hook-payload JSON into each script and assert allow/deny/reset behavior; the live proof is: 3 Edit calls on 3 code files in one turn ⇒ 3rd denied with the delegation message, and `/haejwo:status` shows whether hooks fire inside subagents on this CLI version.
