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

[Claude Code](https://claude.com/claude-code) and [Codex](https://github.com/openai/codex) are already the official coding harnesses: complete, widely used, and best matched to their models. haejwo doesn't replace them — install it and it's on: the **cold-start plugin** that makes **multiple models run well on top of them, automatically**, with no configuration or workflow commands. You just write the ask as a prompt — however roughly, that's the 해줘 — and the host model plans, delegates across cost tiers, debates with an independent reviewer running on a **different vendor's model** when available, reviews, and verifies.

The core idea: keep the expensive main model on **judgment** (plan, delegate, decide, synthesize) and push **execution** to cheap tiers — and don't just ask nicely. A `PreToolUse` hook **physically blocks** the main agent when it starts implementing instead of delegating.

## Install

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
/reload-plugins   # only if a session is already open (a fresh session loads it automatically)
/haejwo:setup     # optional one-time config — safe defaults work without it
```

**Codex CLI** (same repo, same hooks — measured-compatible):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
Trust the hooks once in interactive codex via `/hooks`. Commands surface as `@haejwo-*` skills.

Hooks load at session start, so restart the session (or run `/reload-plugins` on Claude Code) after install. `/haejwo:setup` is optional — it configures model tiers, edit budget, and the reviewer once and persists; safe defaults are already active before you run it, and on first use haejwo offers it automatically.

**Codex is optional** on a Claude Code host — without it, review falls back to the bundled `deep-reasoner` (same-family, weaker independence). **No Opus access?** Run `/haejwo:setup` and pick the `Balanced` or `Budget` tier preset — every role stays within models your account actually has.

Local development install: clone, then `/plugin marketplace add <clone-path>` / `codex plugin marketplace add <clone-path>`.

## What you get

| Feature | What it does |
| --- | --- |
| **Zero-config orchestration** | SessionStart injects the rules and live config automatically. Safe defaults are active immediately: gate ON, 2 files/turn, bash-guard ON |
| **Judgment-first planning** | Feature-scale work starts with plan consensus: the host debates planning, analysis, and review decisions before implementation |
| **Cross-vendor review when available** | With both CLIs installed, the reviewer is the other company's model — codex on Claude Code, claude on Codex |
| **Cheap execution tiers** | The host keeps judgment and stays whatever model your session is using — haejwo never overrides it. Implementation and chores route to cheaper worker tiers (`spawn_agent` model mapping on Codex) |
| **Physical delegation gate** | A PreToolUse gate stops the main agent after **N distinct code files per turn** and blocks main-agent Bash writes to code files. Subagents are exempt; hook errors fail open |
| **Push consent** | Workers never push or deploy. The host asks first unless you grant repo-level auto-push with `/haejwo:push auto` |

Normal use involves **zero haejwo commands** — commands exist only for settings and inspection (`setup`, `status`, `gate`, `push`, plus `plan` as an optional manual trigger).

### Host combinations

| | Claude Code only | Codex only | Both CLIs |
| --- | --- | --- | --- |
| Gate + rules + plan-first + push consent | ✓ | ✓ | ✓ |
| Model tiers (cheap execution, expensive judgment) | ✓ opus/sonnet/haiku | ✓ via `spawn_agent` model mapping (judgment inherits; execution downshifts) | ✓ |
| **Cross-vendor adversarial review** | fallback: same-family `deep-reasoner` | fallback: same-model subagent (weaker independence) | ✓ codex↔claude |

Install the other CLI only if you want different-model review — that's what the second CLI buys (adding Claude Code also buys model tiers). Same-model fallbacks work, but a different model catches what self-review can't.

## Commands (settings & inspection only — the supporting cast)

Normal use needs **none** of these; you just talk. They exist to adjust or inspect the plugin:

| Claude Code · Codex skill | Role |
| --- | --- |
| `/haejwo:setup` · `@haejwo-setup` | First-run configuration — tiers, edit budget, bash-guard, reviewer. Asked once, persisted |
| `/haejwo:status` · `@haejwo-status` | Current config, this turn's edit counter, reviewer readiness, hook observations |
| `/haejwo:gate` · `@haejwo-gate` | Inspect or tune the gate live — budget `N`, `on`/`off` (emergency hatch) |
| `/haejwo:push` · `@haejwo-push` | Per-repo push consent — ask-first until you grant auto |
| `/haejwo:plan` · `@haejwo-plan` | Manual trigger for plan consensus (the host already runs it proactively before feature-scale work) |

## Dual-host parity

One repo, one `hooks.json`, one python core — every codex behavior was **measured, not assumed** (env compat aliases, deny round-trip, `apply_patch` multi-file parsing with atomic whole-patch deny, `turn_id` turn reset, subagent `agent_type` exemption). Codex-side tiers ride the native `spawn_agent` model/effort parameters — the reasoner tier inherits the host model (judgment never silently downgrades); worker and chore tiers downshift.

## Docs

| Doc | What's inside |
| --- | --- |
| [`haejwo/README.md`](haejwo/README.md) | Deep dive: gate semantics, first run, commands, reasoning policy, verification |
| [`haejwo/PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) | The constitution — 12 principles with origin cases, precedence order, amendment rule |
| [`haejwo/PROMPTS.md`](haejwo/PROMPTS.md) | Prompt & style law for every LLM-facing string (deny messages are a tested contract) |

## Non-goals

Boundaries that keep haejwo a lubricant layer on top of the host, not a harness:

- Scheduler, durable task queue, or persistent agent roster
- General DAG or recursive multi-agent runtime
- Model gateway, billing optimizer, or price-based router
- Cross-vendor WORKER routing — worker vendor follows the host; only review crosses vendors (want GPT execution? run the Codex host)
- Worktree orchestration, patch merging, or a replacement sandbox
- Hosted control plane or dashboard
- Autonomous push/deploy/publish
- Workflow DSL or ontology framework
- A second operating architecture (e.g. an advisor-style cheap-main mode)

## Verification

`python3 tests/test_hooks.py` — a hermetic, stdlib-only contract suite: gate counting/dedup/deny wording, concurrency (flock), bash-guard suites, codex `apply_patch` fixtures, turn/stale reset, subagent exemption, manifest version sync, command↔skill mirror drift canaries, rule-text canaries. CI runs it on every push.

## License

[Apache-2.0](LICENSE)
