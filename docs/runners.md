# Reviewer runners: exit codes, knobs, the official Codex plugin

Moved verbatim from the plugin README's [Reviewer runners](../haejwo/README.md#reviewer-runners) section in 2.35.2; usage, guarantees and what is not guaranteed stay there.

**Exit codes:** `0` accepted · `1` failed check (empty reply, failure event, tracing error, repository changed, detection unavailable) · `2` usage error, missing brief, bad env value, refused artifact path, non-git directory (codex runner: unless the sandbox is read-only) · `3` CLI or library missing · `4` no writable temp dir · `124` timeout; other statuses pass through.

**Knobs and config**
- Env wins over config; empty = unset. `CODEX_MODEL`, `CODEX_EFFORT` (`low`/`medium`/`high`/`xhigh`, runner default **`medium`**, `high` for plans/diffs), `CODEX_SANDBOX` (`read-only` default/`workspace-write`/`danger-full-access`), `CODEX_TIMEOUT` (600s at every effort, `0` = unlimited); `CLAUDE_MODEL`, `HJW_CLAUDE_EFFORT` (env > `codex.effort`, else no flag: CLI default), `CLAUDE_TIMEOUT` on the claude runner.
- Keys: `codex.model`, `codex.effort`, `codex.consult_sandbox`. Each runner reads its own install's `config.json`; a foreign value is ignored and disclosed once.

**Official `codex@openai-codex` plugin (1.0.6, measured 2026-09-21):** its rescue subagent is exempt from haejwo's gate and `/codex:review` reported completion without inspecting changes; keep its Stop gate OFF and verify its diffs.
