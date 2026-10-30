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

haejwo is a hooks-and-rules plugin for [Claude Code](https://claude.com/claude-code) and [Codex](https://github.com/openai/codex): it injects orchestration rules at session start, denies the main agent's code edits past a per-turn budget so implementation goes to worker subagents, and, once enabled, sends plans to the other vendor's model for review. You write the ask however roughly (that's the 해줘); the host plans, delegates and verifies. Install it and it's on; setup is optional and nudged until configured.

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
Trust the hooks once via `/hooks`; commands surface as `@haejwo-*` skills. The injected rules load only at session start — restart after install or update.

## What you get

**Judgment stays expensive.** The host is always your session's model and keeps planning, deciding and review. A `PreToolUse` hook denies the main agent's edits past **N distinct code files per turn** (default 2; metadata dirs and out-of-project temp paths exempt) and heuristically blocks its Bash writes to code. On Claude Code, a turn that requested `git push` must end with `Outward actions:` / `Final-diff review:` lines or the Stop hook blocks once (presence only; details: [Contract](haejwo/README.md#contract-234)). Subagents are exempt; hook errors fail open.

**Execution runs at configured tiers.** Implementation and chores go to worker tiers (`default-worker`, `task-worker`, opt-in `Budget`; on Codex a `spawn_agent` mapping). Cost gains come only from cheaper pins chosen after measuring.

**Review comes from another vendor.** Once `/haejwo:setup` enables and verifies it (OFF by default), codex reviews on Claude Code and claude on Codex; otherwise the same-family `deep-reasoner` does (weaker independence). Enabling it sends briefs and the repository content the reviewer reads to that vendor ([disclosure](haejwo/commands/setup.md)).

Cross-vendor review needs both CLIs; the delegation gate, notes and Stop check are Claude Code only. Host combinations and non-goals: [docs/non-goals.md](docs/non-goals.md).

## Commands

Normal use needs **none**: `/haejwo:setup` (one-time config) · `/haejwo:status` (read-only) · `/haejwo:gate` (budget `N`, `on`/`off`, `bash on|off`) · `/haejwo:plan` (the host runs it).

## Links

[Plugin README](haejwo/README.md) ([Contract](haejwo/README.md#contract-234), [By design](haejwo/README.md#by-design)) · [Field record](docs/field/README.md) · [Non-goals](docs/non-goals.md) · [`PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) · [`PROMPTS.md`](haejwo/PROMPTS.md) · [Releases](https://github.com/jungzuna/haejwo/releases)

Tests: `python3 tests/test_hooks.py`; gate commits on its UNPIPED exit code.

## License

[Apache-2.0](LICENSE)
