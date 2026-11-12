# Non-goals

Moved from the root README in 2.35.2. haejwo is a lubricant layer, not a harness:

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
- File-size gates or notes, and comment-deletion passes (3.0 scope F10)
- Interpreter-write detection by regex (`python -c`, heredoc scripts): a real shell parser or nothing
- Preserving the edit budget across hand-back turns (D11, closed in debate 6)
- A memory layer: a preference the user repeats goes to the host's own memory mechanism
- Automatic cheaper tiers: tiers change only by the owner's choice after measuring

## Host combinations

What each host combination gets (moved from the root README; the measured host/path matrix is the plugin README's [Contract](../haejwo/README.md#contract-30)):

| | Claude Code only | Codex only | Both CLIs |
| --- | --- | --- | --- |
| Gate, rules, plan-first, ask-first push | ✓ | ✓ | ✓ |
| Delegation gate (generic agents need an explicit model) | ✓ | — (`spawn_agent` not hooked) | ✓ on Claude Code |
| Model tiers (judgment inherits) | ✓ session model/opus/opus | ✓ `spawn_agent` mapping | ✓ |
| **Cross-vendor review** | fallback: `deep-reasoner` | fallback: same-model subagent | ✓ codex↔claude, after setup verifies |
