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

haejwo is a hooks-and-rules plugin for [Claude Code](https://claude.com/claude-code) and [Codex](https://github.com/openai/codex): it injects orchestration rules at session start, denies the main agent's code edits past a per-turn budget so implementation goes to worker subagents, and, once enabled, sends plans to the other vendor's model for review. It is for users of those harnesses who want the session model kept on judgment.

You write the ask however roughly (that's the 해줘); the host plans, delegates and verifies. Install it and it's on; setup is optional and nudged until configured.

## Install

Requires `python3` (CI tests 3.10); reviewer runners need Bash and git.

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
/reload-plugins   # only if a session is open
/haejwo:setup     # optional — defaults already work
```

**Codex CLI** (same hooks; compatibility measured live, not yet field-tested):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
Trust the hooks once via `/hooks`; commands surface as `@haejwo-*` skills.

`/reload-plugins` (Claude Code) refreshes hooks, commands and agents in an open session; the injected rules re-load only at session start — restart after install or update. Local install: clone, then `/plugin marketplace add <clone-path>` (or the `codex` equivalent).

## What you get

**Judgment stays expensive.** The host — always your session's model, never re-pointed — keeps planning, deciding and review. Feature-scale work should start from a reviewer-critiqued plan (a norm; reviewer off → same-model critique). A `PreToolUse` hook denies the main agent's edits past **N distinct code files per turn** (default 2; known edit tools, any listed code extension in or out of the project; metadata dirs and out-of-project temp paths exempt) and heuristically blocks its Bash writes to code. Subagents are exempt; hook errors fail open.

**Execution runs at configured tiers.** Implementation and chores go to worker tiers — on Claude Code `default-worker` (Opus, high effort), `task-worker` (Opus, low), `Budget` (sonnet/haiku) opt-in; on Codex a `spawn_agent` mapping that inherits the host model by default. Cost gains come only from cheaper pins set after measuring.

**Review comes from another vendor.** Once `/haejwo:setup` enables and verifies it (OFF by default), the reviewer is the other company's model: codex on Claude Code, claude on Codex. Without that CLI or verification, review falls back to the same-family `deep-reasoner` (weaker independence). Enabling it sends briefs and the repository content the reviewer reads to that vendor ([disclosure](haejwo/commands/setup.md)).

Deep dive: [`haejwo/README.md`](haejwo/README.md) · [`PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) · [`PROMPTS.md`](haejwo/PROMPTS.md).

### Host combinations

| | Claude Code only | Codex only | Both CLIs |
| --- | --- | --- | --- |
| Gate, rules, plan-first, ask-first push | ✓ | ✓ | ✓ |
| Delegation gate (generic agents need an explicit model) | ✓ | — (`spawn_agent` not hooked) | ✓ on Claude Code |
| Model tiers (judgment inherits) | ✓ session model/opus/opus | ✓ `spawn_agent` mapping | ✓ |
| **Cross-vendor review** | fallback: `deep-reasoner` | fallback: same-model subagent | ✓ codex↔claude, after setup verifies |

## Commands (settings & inspection)

Normal use needs **none**: `/haejwo:setup` (one-time config) · `/haejwo:status` (read-only status) · `/haejwo:gate` (budget `N`, `on`/`off`) · `/haejwo:plan` (manual trigger; the host runs it by default).

## Non-goals

A lubricant layer, not a harness:

- Scheduler, durable task queue, or persistent agent roster
- General DAG or recursive multi-agent runtime
- Model gateway, billing optimizer, or price-based router
- Cross-vendor WORKER routing — workers follow the host's vendor (for GPT execution, run the Codex host); `codex@openai-codex` can coexist, but reviews keep haejwo's non-editing runner
- Worktree orchestration or patch merging for workers
- Hosted control plane or dashboard
- Autonomous push/deploy/publish
- Workflow DSL or ontology framework
- A second operating architecture (advisor-style cheap-main mode)
- Hard gates on judgment calls (plan markers, report length) — never hook-gated; a worker without a plan marker asks once and reports blocked if no actionable plan or reason follows

## Verification

`python3 tests/test_hooks.py` — the contract suite (Python stdlib only; needs bash, git, a Unix host, and this repo's git history for two tests). Gate any commit on its UNPIPED exit code; CI runs it on every push.

Release notes: [Releases](https://github.com/jungzuna/haejwo/releases) · [tags](https://github.com/jungzuna/haejwo/tags).

## License

[Apache-2.0](LICENSE)
