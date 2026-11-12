# Reviewer runners: exit codes, knobs, the official Codex plugin

Moved verbatim from the plugin README's [Reviewer runners](../haejwo/README.md#reviewer-runners) section in 2.35.2; usage, guarantees and what is not guaranteed stay there.

**Exit codes:** `0` accepted · `1` failed check (empty reply, failure event, tracing error, repository changed, detection unavailable, receipt not written) · `2` usage error, missing brief, bad env value, refused artifact path, non-git directory (codex runner: unless the sandbox is read-only) · `3` CLI or library missing · `4` no writable temp dir · `124` timeout; other statuses pass through.

**Knobs and config**
- Env wins over config; empty = unset. `CODEX_MODEL`, `CODEX_EFFORT` (`low`/`medium`/`high`/`xhigh`, runner default **`medium`**, `high` for plans/diffs), `CODEX_SANDBOX` (`read-only` default/`workspace-write`/`danger-full-access`), `CODEX_TIMEOUT` (600s at every effort, `0` = unlimited); `CLAUDE_MODEL`, `HJW_CLAUDE_EFFORT` (env > `codex.effort`, else no flag: CLI default), `CLAUDE_TIMEOUT` on the claude runner.
- `HJW_REVIEW_BASE` (3.0.0, both runners, optional): the base the caller reviews against (a ref, a commit, any text), copied verbatim into the reply's receipt as `base (caller-supplied, not verified): <value>`; unset or empty = `base: unavailable`. The runner neither resolves nor checks it.
- Keys: `codex.model`, `codex.effort`, `codex.consult_sandbox`. Each runner reads its own install's `config.json`; a foreign value is ignored and disclosed once.

**Review receipt (3.0.0):** on success the reply file is the receipt block, a blank line, then the reviewer's bytes unchanged. Lines: `repository:` (the change-detection root, transported losslessly, escaped to one line), `base:` or `base (caller-supplied, not verified):`, `HEAD:`, `snapshot: schema=1 sha256=<hex> truncated=<0|1> unreadable=<n>` (SHA-256 over the canonical JSON of the BEFORE snapshot; schema 1 = `lib/detect.py`'s snapshot fields), `limitations:`. Outside git every observed field is `unavailable`. A failed run writes no receipt. A receipt that cannot be written (helper failure or timeout, reply not reopenable) FAILS the run (exit 1) and leaves the reviewer's answer as returned. Only the LEADING receipt block of a successful invocation has runner provenance; a receipt-like block later in a reply, or anywhere in a failed run's answer file, is reviewer text.

**Official `codex@openai-codex` plugin (1.0.6, measured 2026-09-21):** its rescue subagent is exempt from haejwo's gate and `/codex:review` reported completion without inspecting changes; keep its Stop gate OFF and verify its diffs.
