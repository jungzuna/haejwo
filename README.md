<p align="center">
  <img src="assets/haejwo.png" width="520" alt="haejwo — the expensive model lounges and says 해줘 while the worker tiers do the work">
</p>

<h1 align="center">해줘</h1>

<p align="center"><strong>haejwo — "just handle it."</strong><br><em>you talk; the models work it out among themselves.</em></p>

<p align="center">
  <img src="https://img.shields.io/github/v/tag/jungzuna/haejwo?label=version&color=111111&style=flat-square" alt="version">
  <img src="https://img.shields.io/badge/hosts-Claude%20Code%20%C2%B7%20Codex-111111?style=flat-square" alt="hosts">
  <img src="https://img.shields.io/badge/license-Apache--2.0-111111?style=flat-square" alt="Apache-2.0">
</p>

<p align="center"><sub><a href="README.ko.md">한국어</a></sub></p>

[Claude Code](https://claude.com/claude-code) and [Codex](https://github.com/openai/codex) are the official coding harnesses; haejwo doesn't replace them. Install it and it's on — the **cold-start plugin** that makes **multiple models run well on top of them**, with no configuration or workflow commands.

You write the ask as a prompt, however roughly (that's the 해줘); the host plans, delegates across tiers and verifies — and, once setup enables the reviewer, debates with the other vendor's model.

## Install

Requires `python3` (CI tests 3.10); the reviewer runners also use Bash and git. Cross-vendor review needs the other vendor's CLI and `/haejwo:setup` verification.

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
/reload-plugins   # only if a session is open
/haejwo:setup     # optional — safe defaults already work
```

**Codex CLI** (same repo, same hooks; hook compatibility measured live, not yet exercised in real project work):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
Trust the hooks once via `/hooks`; commands surface as `@haejwo-*` skills.

Hooks load at session start — restart the session after install. Local install: clone, then `/plugin marketplace add <clone-path>` (or the `codex` equivalent).

## What you get

**Judgment stays expensive.** The host is always your session's model — haejwo never re-points it — and keeps planning, deciding and review. Feature-scale work starts from a debated plan, and a `PreToolUse` hook **physically** denies the main agent past **N distinct code files per turn** (default 2) and blocks its Bash writes to code. Subagents are exempt; hook errors fail open.

**Execution runs at configured tiers.** Implementation and chores route to configured worker tiers — Opus for `default-worker`, Opus at low effort for `task-worker`, `Budget` (sonnet/haiku) as the opt-in on Claude Code; `spawn_agent` mapping on Codex. Safe defaults (gate ON, 2 files/turn, bash-guard ON) run from the first session, so `/haejwo:setup` is optional.

**Review comes from another vendor.** With both CLIs installed — and `/haejwo:setup` run to enable and verify the reviewer, which is OFF by default — the reviewer is the other company's model: codex on Claude Code, claude on Codex. Without the second CLI, or before that verification, review falls back to the same-family `deep-reasoner` (weaker independence).

Deep dive: [`haejwo/README.md`](haejwo/README.md) · [`PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) · [`PROMPTS.md`](haejwo/PROMPTS.md).

### Host combinations

| | Claude Code only | Codex only | Both CLIs |
| --- | --- | --- | --- |
| Gate, rules, plan-first, push consent | ✓ | ✓ | ✓ |
| Model tiers (judgment inherits) | ✓ session model/opus/opus | ✓ `spawn_agent` mapping | ✓ |
| **Cross-vendor review** | fallback: `deep-reasoner` | fallback: same-model subagent | ✓ codex↔claude, after setup verifies |

## Commands (settings & inspection)

Normal use needs **none** of these.

| Claude Code · Codex skill | Role |
| --- | --- |
| `/haejwo:setup` · `@haejwo-setup` | One-time config — tiers, budget, bash-guard, reviewer |
| `/haejwo:status` · `@haejwo-status` | Config, this turn's counter, reviewer readiness, observations |
| `/haejwo:gate` · `@haejwo-gate` | Tune the gate live — budget `N`, `on`/`off` |
| `/haejwo:push` · `@haejwo-push` | Per-repo push consent — ask-first until granted |
| `/haejwo:plan` · `@haejwo-plan` | Manual trigger for plan consensus (host-run by default) |

## Non-goals

Boundaries that keep haejwo a lubricant layer, not a harness:

- Scheduler, durable task queue, or persistent agent roster
- General DAG or recursive multi-agent runtime
- Model gateway, billing optimizer, or price-based router
- Cross-vendor WORKER routing — worker vendor follows the host (want GPT execution? run the Codex host); `codex@openai-codex` can coexist, but reviews keep haejwo's non-editing consult runner
- Worktree orchestration or patch merging for workers
- Hosted control plane or dashboard
- Autonomous push/deploy/publish
- Workflow DSL or ontology framework
- A second operating architecture (advisor-style cheap-main mode)
- Hard gates on judgment calls (plan markers, report length) — norms and nudges only

## Verification

`python3 tests/test_hooks.py` — a hermetic, stdlib-only contract suite: gate counting and deny wording, concurrency, bash-guard, codex `apply_patch`, turn reset, manifest sync, mirror drift, rule canaries. Gate any commit on its UNPIPED exit code; CI runs it on every push.

See [Releases](https://github.com/jungzuna/haejwo/releases) for release notes and [tags](https://github.com/jungzuna/haejwo/tags) for every version.

## License

[Apache-2.0](LICENSE)
