<p align="center">
  <img src="assets/haejwo.png" width="520" alt="haejwo — the expensive model lounges and says 해줘 while the small worker tiers sweat through the actual tasks">
</p>

<h1 align="center">해줘</h1>

<p align="center"><strong>haejwo — "just handle it."</strong><br><em>you talk; the models work it out among themselves.</em></p>

<p align="center">
  <img src="https://img.shields.io/github/v/tag/jungzuna/haejwo?label=release&color=111111&style=flat-square" alt="release">
  <img src="https://img.shields.io/badge/hosts-Claude%20Code%20%C2%B7%20Codex-111111?style=flat-square" alt="hosts">
  <img src="https://img.shields.io/badge/license-Apache--2.0-111111?style=flat-square" alt="Apache-2.0">
</p>

<p align="center"><sub><a href="README.ko.md">한국어</a></sub></p>

A **lubricant harness** for [Claude Code](https://claude.com/claude-code) and [Codex CLI](https://github.com/openai/codex): you just say what you want — however roughly, that's the 해줘 — and multiple models run well together underneath. The host model plans with an independent reviewer, delegates across cost tiers, reviews, and verifies. No workflow commands to learn.

The core idea: keep the expensive main model on **judgment** (plan, delegate, decide, synthesize) and push **execution** to cheap tiers — and don't just ask nicely. A `PreToolUse` hook **physically blocks** the main agent when it starts implementing instead of delegating.

## How it works

| Layer | What it does |
| --- | --- |
| **Declaration** | A SessionStart hook injects the orchestration rules + live config into every session |
| **Roles** | `deep-reasoner` (opus) · `default-worker` (sonnet) · `task-worker` (haiku) · an independent reviewer that is **a different model** whenever the other CLI is available — codex on a Claude host, claude on a Codex host |
| **Criteria** | Injected rules: when the main agent handles directly vs must delegate; plan-first consensus before feature-scale work |
| **Enforcement** | PreToolUse gate: max **N distinct code files per turn** (default 2) for the main agent — the N+1th edit is *denied* with a delegation instruction. Bash writes to code files are denied outright. Subagents are exempt; every denial says exactly what to do instead; any hook error fails open |

Normal use involves **zero haejwo commands** — commands exist only for settings and inspection (`setup`, `status`, `gate`, `push`, plus `plan` as an optional manual trigger).

## Install

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
```

**Codex CLI** (same repo, same hooks — measured-compatible):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
Trust the hooks once in interactive codex via `/hooks`. Commands surface as `@haejwo-*` skills.

**Codex is optional** on a Claude Code host — without it, independent review falls back to the bundled `deep-reasoner`. **No Opus access?** Run `/haejwo:setup` and pick the `Balanced` or `Budget` tier preset — every role stays within models your account actually has.

### What you get per setup

| | Claude Code only | Codex only | Both CLIs |
| --- | --- | --- | --- |
| Gate + rules + plan-first + push consent | ✓ | ✓ | ✓ |
| Model tiers (cheap execution, expensive judgment) | ✓ opus/sonnet/haiku | ✓ via `spawn_agent` model mapping (judgment inherits; execution downshifts) | ✓ |
| Independent review by a **different** model | fallback: same-family `deep-reasoner` | fallback: same-model subagent (weaker independence) | ✓ codex↔claude |

Install the other CLI only if you want different-model review — that's what the second CLI buys (adding Claude Code also buys model tiers). Same-model fallbacks work, but a different model catches what self-review can't.

Hooks load at session start — restart (or `/reload-plugins` on Claude Code) after install. On first run, haejwo nudges once toward `/haejwo:setup` (interactive model-tier / budget / reviewer configuration, persisted — asked once, never again). Safe defaults are active even before setup.

Local development install: clone, then `/plugin marketplace add <clone-path>` / `codex plugin marketplace add <clone-path>`.

## Commands (settings & inspection only — the supporting cast)

Normal use needs **none** of these; you just talk. They exist to adjust or inspect the harness:

| Claude Code · Codex skill | Role |
| --- | --- |
| `/haejwo:setup` · `@haejwo-setup` | First-run configuration — tiers, edit budget, bash-guard, reviewer. Asked once, persisted |
| `/haejwo:status` · `@haejwo-status` | Current config, this turn's edit counter, reviewer readiness, hook observations |
| `/haejwo:gate` · `@haejwo-gate` | Inspect or tune the gate live — budget `N`, `on`/`off` (emergency hatch) |
| `/haejwo:push` · `@haejwo-push` | Per-repo push consent — ask-first until you grant auto |
| `/haejwo:plan` · `@haejwo-plan` | Manual trigger for plan consensus (the host already runs it proactively before feature-scale work) |

## Dual-host parity

One repo, one `hooks.json`, one python core — every codex behavior was **measured, not assumed** (env compat aliases, deny round-trip, `apply_patch` multi-file parsing with atomic whole-patch deny, `turn_id` turn reset, subagent `agent_type` exemption). The independent reviewer inverts per host so review always comes from a different model family. Codex-side tiers ride the native `spawn_agent` model/effort parameters — the reasoner tier inherits the host model (judgment never silently downgrades); worker and chore tiers downshift.

## Docs

| Doc | What's inside |
| --- | --- |
| [`haejwo/README.md`](haejwo/README.md) | Deep dive: gate semantics, first run, commands, reasoning policy, verification |
| [`haejwo/PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) | The constitution — 12 principles with origin cases, precedence order, amendment rule |
| [`haejwo/PROMPTS.md`](haejwo/PROMPTS.md) | Prompt & style law for every LLM-facing string (deny messages are a tested contract) |

## Verification

`python3 tests/test_hooks.py` — a hermetic, stdlib-only contract suite: gate counting/dedup/deny wording, concurrency (flock), bash-guard suites, codex `apply_patch` fixtures, turn/stale reset, subagent exemption, manifest version sync, command↔skill mirror drift canaries, rule-text canaries. CI runs it on every push.

## License

[Apache-2.0](LICENSE)
