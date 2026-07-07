# haejwo (해줘) — the lubricant harness

> **"just handle it."** — you talk; the models work it out among themselves.

Claude Code is already a great harness. haejwo is the thin layer that makes **multiple models run well on top of it — automatically**. You don't drive it with commands: you just say what you want (however roughly — that's the 해줘), and the host plans with an independent reviewer, delegates across cost tiers, reviews, and verifies.

Underneath, it keeps the expensive main model on **judgment** (plan, delegate, decide, synthesize) and pushes **execution** to cheap tiers — and doesn't just ask nicely: a PreToolUse hook **physically blocks** the main agent when it starts implementing instead of delegating.

The 4-layer design: *declaration + role assignment + operating criteria + enforcement* — the point is not making the model work smart; it's making the expensive model **unable** to do cheap work.

## The 4 layers

| Layer | Artifact | What it does |
|---|---|---|
| Declaration | SessionStart hook (`session_brief.py`) | Injects the orchestration rules + live config into every session |
| Roles | `agents/` | `deep-reasoner` (opus) · `default-worker` (sonnet) · `task-worker` (haiku) + codex slot (`scripts/codex_consult.sh`, read-only outside perspective) |
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
- Known bypass gap (accepted): `python -c` / `node -e` style writes are regex-invisible; the injected rules forbid them by instruction.

## First run
`SessionStart` nudges once: run **`/haejwo:setup`** — interactive choices (AskUserQuestion) for model tiers, edit budget, bash-guard, codex reviewer; probes `codex login status` and smoke-tests the codex slot read-only; persists to `${CLAUDE_PLUGIN_DATA}/config.json` (survives plugin updates — asked once, never again). Safe defaults are active even before setup: gate ON, 2 files/turn, bash-guard ON. If Opus isn't available on the account, pick the `Balanced` or `Budget` preset — every role stays within reachable models.

## Zero-command by design
Normal use involves **no haejwo commands at all**. You talk; the host does the rest automatically:
- Feature-scale ask → the host runs the **planning consensus** procedure itself (independent-reviewer debate → agreed plan) before implementing — `/haejwo:plan` exists only as an optional manual trigger.
- Implementation → delegated to the right tier; the gate enforces it when the host forgets.
- Push/deploy → host asks once; say "do it automatically from now on" and it records the grant.

Commands are for **settings and inspection only** (below). The name-integrity rule: the moment users must **understand or manage the harness** — delegation, reviewer consensus, edit limits, tiers, or recovery commands — to get their work done, 해줘 stops being true. Any change that requires that is off-concept.

## Commands
| Command | Role |
|---|---|
| `/haejwo:plan <topic>` | Pre-implementation consensus: independent-reviewer debate → agreed plan (conversation-first; file only on request); feature-scale briefs embed it (`Plan:` section) |
| `/haejwo:setup` | First-run (or re-run) interactive configuration + codex probe |
| `/haejwo:status` | Config, this turn's counter, codex readiness, subagent-hook observations |
| `/haejwo:gate [on\|off\|N\|bash on\|bash off]` | Emergency hatch / live tuning |
| `/haejwo:push [auto\|ask]` | Per-repo push consent — outward actions are host-owned, ask-first until granted (registry, not a gate) |

(Cross-session/project activity stays auditable without a command: the gate hooks record every fire — actor, instance, file — in `state/observations.jsonl`; ask the host to analyze it when needed.)

Env override for a single command: `HAEJWO_GATE=off <cmd>`.

**Reasoning policy:** reviewer effort scales with the decision's stakes — `medium` for routine checks, `high` (runner default) for standard consults, `xhigh` reserved for architecture forks / security-critical calls / final deadlock rounds; non-reasoning probes stay explicit `low`. The claude reviewer path uses `deep-reasoner` (opus, high effort). Uniform max dilutes budget exactly where judgment compounds.

## Install

See the [root README](../README.md) for install (GitHub or local-clone marketplace add, both hosts). Codex: trust the hooks once in interactive codex via `/hooks`; commands surface as `@haejwo-*` skills. (CI-only: headless pipelines may pass `--dangerously-bypass-hook-trust` — never needed, and not recommended, for interactive use.)

Hooks load at session start — restart the session (or `/reload-plugins` on Claude Code) after install.

**Dual-host parity:** gate (apply_patch-aware, whole-patch atomic deny), bash-guard (codex names its shell tool `Bash` too), rules injection, turn reset (`turn_id`), worker exemption (codex subagents carry the same `agent_type`/`agent_id` fields — measured), and the independent reviewer inverts per host: codex_consult.sh on Claude, **claude_consult.sh on Codex** (principle 9: a different model). Codex-side tiers ride the native `spawn_agent` model/effort parameters — judgment inherits the host model; execution downshifts (`models_codex` in config).

Optional hardening (README-only, not auto-applied): add `permissions.deny` rules for `Bash(sed:*)` etc. and deny `Read` of the state dir to prevent tampering.

## Conventions
The constitution lives in [`PHILOSOPHY.md`](PHILOSOPHY.md) — 12 principles with their origin cases, the precedence order for conflicts, and the docs map. Read it before changing ANYTHING. Prompt & style policy lives in [`PROMPTS.md`](PROMPTS.md) — every prompt surface (commands, agents, rules, hook-emitted messages, script text) follows it; deny-message strings are a tested contract.

## Verification
`scripts/` are plain python3 (stdlib only). Synthetic tests pipe hook-payload JSON into each script and assert allow/deny/reset behavior; the live proof is: 3 Edit calls on 3 code files in one turn ⇒ 3rd denied with the delegation message, and `/haejwo:status` shows whether hooks fire inside subagents on this CLI version.
