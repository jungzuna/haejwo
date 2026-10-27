# haejwo (해줘) — the cold-start plugin

haejwo keeps the main model on **judgment** and sends **execution** to worker tiers on Claude Code and Codex. Defaults: Claude Code worker tiers are Opus, Codex workers inherit the host model, the reviewer is OFF until setup verifies it.

## The 4 layers

| Layer | Artifact | What it does |
|---|---|---|
| Declaration | SessionStart hook (`session_brief.py`) | Injects the rules (a minimal core if unreadable or over budget), a setup nudge until configured, and the config summary |
| Roles | `agents/` | `deep-reasoner` (fresh context, session model and effort, never below default-worker) · `default-worker` (opus, high effort) · `task-worker` (opus, low effort) + the reviewer slot (`scripts/codex_consult.sh` on Claude, `scripts/claude_consult.sh` on Codex) |
| Criteria | `rules/orchestration.md` | When the main agent delegates |
| **Enforcement** | PreToolUse hooks (`gate.py`, `bash_guard.py`, `delegation_gate.py`) + a Stop hook (`report_check.py`); re-presentation via the PostToolUse hook `post_bash.py` | Main agent: over-budget code edits, Bash code writes and generic-agent delegation without a model are **denied**; on Claude Code a final message lacking the record labels after a detected `git push` is **blocked once** (Contract below); re-presentation adds notes, never denials |

## Contract (2.34)
A bounded, supported version. It cannot eliminate the host's completion drive or accept-all behavior, prevent same-model reviews from being labelled independent, make broad assent authorize actions or sequences outside the enumerated list, or guarantee equivalent behavior across unhooked or unmeasured Codex host paths.

- **Enforced** (main agent; fail-open; exempt: subagents, `HAEJWO_GATE=off`, `gate.enabled` false, malformed config): the file budget (`gate.py`: edits past **N distinct code files per turn**, default 2; a path with a component in `exempt_dir_components` is not code despite a code extension (checked after the temp exemption, before the extension)); Bash code writes (`bash_guard.py`, a heuristic); the model pin (`delegation_gate.py`: general-purpose/Explore need an explicit model; unhooked on Codex); plan-marker PRESENCE, recorded by `delegation_gate.py` (not plan adequacy); main-agent push detection (`bash_guard.py`) → note → `outward-attempt` → Stop block ONCE (`report_check.py`; Record check below): exactly a presence check of two labels, verifying neither authorization, push success nor review adequacy. Per guard, off: `gate.bash_guard=false` (Bash code-write deny, push detection, runner/push notes), `gate.delegation_guard=false` (model pin, plan-marker record), `gate.report_check=false` (Stop block).
- **Re-presented** only where delivery was demonstrated (notes, never denials; Notes below): runner-call note, push-returned note, Stop FACTS line. Delivery demonstrated is not benefit demonstrated.
- **Norm only** (rules, no mechanism); last measured (field round 4, one session, same-vendor audit):

| Obligation | Omitted / opportunities |
|---|---|
| Outward-action approval scope and order | 0 opportunities |
| Final risk re-review of fixed diffs | 1/5 (disclosed) |
| Per-finding judgment (fact vs remedy, reasons) | >= 34/60 late or unrecorded |
| Acceptance-report closing statement, independence | 1/1 partial |
| Provenance of cited numbers | 0/~8 |
| Scratch/probe scripts delegated | 6/6 host-run |

- **Measured paths:** Claude Code 2.1.305 (Stop block); 2.1.310 headless (UserPromptSubmit/PostToolUse delivery, push shapes S1/S2); 2.1.311 headless via a `--plugin-dir` checkout, not the installed cache: four 2.34.0 canaries passed 2026-10-27 (docs/field/2026-10-27-canaries-2034.md). Interactive paths, Codex hosts: notes and Stop check unverified (Codex gate parity: Install).
- **Everything else is unverified.** Behavior changes only on the reopen triggers ([K4](../docs/field/2026-10-27-consensus-finish.md)): "T1 — a confirmed material safety or authorization incident caused or materially enabled by the plugin, or demonstrating failure of its declared coverage; or a reproducible material regression against the frozen contract … T2 — demonstrated failure of a declared supported host/path assumption. A new upstream version alone is not demonstrated drift." Else: [`docs/field/BACKLOG.md`](../docs/field/BACKLOG.md) (reject option per item).

## Gate semantics

- Counts **distinct code files** (config extension list) per user turn; re-editing is free. **Subagents are exempt** (payload `agent_id`/`agent_type`); workers may delegate further (nested agents), exempt alike from every gate and note.
- Turn state is per session id, sanitized and cut to 80 chars: ids alike in those chars, or absent (`unknown`), share a counter.
- Denials name the budget and delegate; the last allowed edit warns the budget is full.
- **Fail-open**: any hook error or ambiguity ⇒ no objection. haejwo never approves a tool call, only denies or annotates, so your permission settings still apply. An affordance, not a security boundary (P4): editing `config.json` (not code) turns the gate off; dynamically written code is invisible to the guard.
- The temp exemption is decided by where a path RESOLVES relative to the project, not by what the file is: a path resolving under a temp dir (`/tmp`, `/var/tmp`, `tempfile.gettempdir()`) outside the active project (git toplevel, else cwd) is not code, scratch code included; a temp-dir repository that IS the project is gated unless its root is a temp dir or ancestor (`/tmp`, `/`). In-place editors run via `find`/`xargs` are denied on any path.
- **Record check (Stop, 2.32):** after the bash guard passed a main-agent `git push` this turn (noted to the model, recorded as `outward-attempt`), a final message lacking `Outward actions:` or `Final-diff review:` is blocked ONCE naming both; `not run because:` satisfies the review label. Presence only. Exempt: `HAEJWO_GATE=off`, `gate.enabled`/`gate.report_check` false, malformed config, subagents, Codex hosts (unmeasured), `stop_hook_active`, a prior nudge, a failed state write. Detection is a grammar, not the shell: dry-runs (`-n`, `--dry-run`) are not attempts; quoted-delimiter heredoc bodies are skipped, what follows scanned. Missed: unquoted-delimiter bodies and what follows them, continued headers, `$(…)` in bodies, unlisted wrappers (`sudo git push`). A turn idle past the 2 h stale reset can be nudged twice. Measured on Claude Code 2.1.305: the block shows as a "Stop hook error", then the model wrote both lines. Acceptance reports without a push are unchecked (not countable).
- **Notes (2.34):** notes, never denials, each pairing ONE existing obligation with event facts, from `post_bash.py` only. Per detected runner call: `[haejwo] detected runner call ({runner}; tool call {id}): command syntax only — whether it ran, finished or reviewed anything is not established.`, then the reply-reading obligation (per-finding fact vs remedy, the closing statement, same-model ≠ independent). One per tool call id; dedupe keeps the newest 64 ids (an evicted id replayed emits again); a setup probe (an `HJW_PROBE=` assignment word before the runner's command word) gets none. After a detected push-bearing Bash call returned: tool call id; quoted shell output tail (the last line non-empty after sanitizing, non-printables removed, URL credentials redacted, 160 chars). The Stop block reason adds a FACTS line: the count and tool call ids of detected push-bearing calls. Detected command text, execution and remote outcome are three things: `false && git push` is detected and may never run; a returned call is not a successful push; no hook classifies output. Bound per session ≈ runner calls × ≤ 400 + push calls × ≤ 390 + Stop blocks × ≤ 600 chars, measured with 30-char tool-call ids and 2-digit counts; longer ids lengthen the line proportionally (ids are not truncated); each emission logged with its length; a note needs its dedupe claim saved first (lock busy or save failed: no note). `gate.bash_guard` gates `post_bash.py`; `gate.turn_checklist` is gone (an old key in config.json is preserved but unused). Exempt: Codex hosts (unmeasured), subagents, `HAEJWO_GATE=off`, `gate.enabled` false, malformed config. Missed: wrapped runners (`bash -c '…consult.sh'`, `bash <path>`), failed tool calls (PostToolUseFailure unmeasured: no note); push-bearing call ids beyond 64 per session get no return note and no dedupe.
- **Measured host facts** (measured on Claude Code 2.1.310 headless; roles per the CLI transcript): UserPromptSubmit and PostToolUse context reach the model (UserPromptSubmit's persists into later turns); PreToolUse notes land after the tool ran, with PostToolUse's, so hook notes report and prevent nothing; any Stop output costs one extra model turn; SubagentStop output goes to the subagent and REPLACES its answer, so haejwo never uses it. Host version observed 2.1.311 on 2026-10-27; measurements are per version.

## First run
**`/haejwo:setup`** is optional, nudged each session until configured. It sets tiers, budget, bash-guard and the reviewer (probed, smoke-tested) in `${CLAUDE_PLUGIN_DATA}/config.json`. Before setup, defaults apply (gate ON, 2 files/turn, bash-guard ON) unless `/haejwo:gate` stored a value. Presets: `Standard` / `Budget` (Sonnet/Haiku) / `Custom`.

On Claude Code a worker runs at its agent file's model unless one is passed; the delegation gate steers an omitted override that misses a configured pin but never verifies which model ran.

## Commands
Normal use needs **none**: the host plans, delegates and asks before push/deploy (prior assent covers only the actions and order it enumerates); acceptance and outward reports end with the rules' two-line record, whose presence proves neither adequacy nor authorization. Credential decisions include `.env` sourcing/printing, inline secrets, secret manifest copies; a comment is not a control, a test covers only itself.

| Command | Role |
|---|---|
| `/haejwo:plan <topic>` | Pre-implementation consensus: reviewer critique → host-decided plan; feature-scale briefs embed it (`Plan:`) |
| `/haejwo:setup` | Interactive configuration + reviewer probe |
| `/haejwo:status` | Config, turn counter, reviewer readiness, this session's observations and delegations (read-only) |
| `/haejwo:gate [on\|off\|N\|bash on\|bash off]` | Emergency hatch / live tuning |

Gate fires log to `state/observations.jsonl`. `HAEJWO_GATE=off` works only in the host process's environment (no per-command bypass); `/haejwo:gate off` is the hatch.

## Install
See the [root README](../README.md); Codex in headless CI only: `--dangerously-bypass-hook-trust`.

`/reload-plugins` (Claude Code) refreshes hooks, commands and agents, not the injected rules — restart after install/update.

**Support floor:** runners support installs >= 2.22.0; a 2.26+ runner never forwards below it (says so, runs locally). Pre-floor installs are unsupported as sources too: without the `snapshot.py` tombstone, a pre-floor runner stays local.

**Dual-host parity (measured):** all but the delegation gate (Codex `spawn_agent` is unhooked), the Stop record check and the notes (silent on a Codex host; unmeasured there) works on both hosts, the gate apply_patch-aware; Codex tiers ride `spawn_agent` parameters (`models_codex`).

## Reviewer runners
**Usage:** `<runner> [-o out.md] brief.md`, or `echo … | <runner> -` (stdin brief, deleted on exit), from the project root. Without `-o` the reply is `<brief>.reply.md`, the log beside it; keep replies and briefs until the acceptance is reported (cleanup must not delete them). For a stable tree, pause writes or review a prepared worktree.

**Exit codes:** `0` accepted · `1` failed check (empty reply, failure event, tracing error, repository changed, detection unavailable) · `2` usage error, missing brief, bad env value, refused artifact path, non-git directory · `3` CLI or library missing · `4` no writable temp dir · `124` timeout; other statuses pass through.

**Guarantees**
- A standing non-editing REVIEWER CONTRACT is prepended to every brief and asks for a closing statement (material read, verification run, omissions); `claude_consult.sh` also disallows Edit/Write/NotebookEdit.
- Post-run change detection FAILS the run when the repository changed: HEAD, tracked files, untracked files (contents for the first 2000). Runner artifacts are excluded.
- **Artifact guard:** a reply, log, events or temp-brief path inside the worktree or its git dirs (lexically or via a symlink), or an existing hard-linked artifact, is refused (exit 2) before any write or paid call. Other worktrees of the repository are not covered.
- A codex model rejected before execution fails with one hint, unretried.
- Fail-closed: a git error, unreadable snapshot or timeout fails the run (before the paid call when the BEFORE snapshot fails); unreadable files are disclosed (`some files unreadable: N`).
- Python helpers and the reviewer CLI run under a python3 wall clock; the bootstrap python, coreutils, log scans, file copies and stdin-brief reads do not. Model and effort print with their source.

**Not guaranteed**
- **Not a security boundary:** Bash stays available to the reviewer.
- Never covered: package installs, MCP/user/global config, ignored or out-of-repo files, edit-then-restore. Under concurrent writers a change has **attribution unknown**.
- **Review coverage is what the reviewer read.** Without repo access it sees only the brief; a scope-limited review names its scope and omissions. The codex result line's `shell: N completed commands` does not establish coverage; `file access unverified (0 shell commands)` means file access is UNVERIFIED, not that no files were read; the header's `scope=` (`scope=none (change detection off)` outside git) is the root change detection covers, not what the reviewer read.
- A failure reported only in prose is NOT detected (measured 2026-09-21); the host reads every reply. `claude_consult.sh` has no event classifier.
- An unselected model is **`cli-default (identity unverified)`**; the Codex-host workflow is field-untested.
- The `codex` block names the reviewer of the data dir's host — on a Codex host `codex.model` is a Claude model; a rename is deferred as schema debt.

**Knobs and config**
- Env wins over config; empty = unset. `CODEX_MODEL`, `CODEX_EFFORT` (`low`/`medium`/`high`/`xhigh`, runner default **`medium`**, `high` for plans/diffs), `CODEX_SANDBOX` (`read-only` default/`workspace-write`/`danger-full-access`), `CODEX_TIMEOUT` (600s at every effort, `0` = unlimited); `CLAUDE_MODEL`, `HJW_CLAUDE_EFFORT` (env > `codex.effort`, else no flag: CLI default), `CLAUDE_TIMEOUT` on the claude runner.
- Keys: `codex.model`, `codex.effort`, `codex.consult_sandbox`. Each runner reads its own install's `config.json`; a foreign value is ignored and disclosed once.

**Official `codex@openai-codex` plugin (1.0.6, measured 2026-09-21):** its rescue subagent is exempt from haejwo's gate and `/codex:review` reported completion without inspecting changes; keep its Stop gate OFF and verify its diffs.

## By design
Rejected recurring objections:
- The host's budget applies despite same-model workers: it steers implementation toward workers by capping the code files edited per turn — not context size or model price.
- The budget counts allowed attempts, not successful edits: one hook.
- Runners do not check `enabled`/`verified_at`: the host is the policy gate, the runner a tool.
- A stored `danger-full-access` consent persists; every run header discloses it. The Claude reviewer keeps Bash for tests and greps; detected repository changes fail the run (documented exclusions apply).
- Stale cache-path runners forward to the installed version (Claude Code only): restarting pushes a measured host failure onto users; the support floor bounds the cost.
- The runner stack keeps the artifact guard and change detection: plain status/diff checks omit their contracts.
- The bash guard covers redirects, `tee` and in-place editors only — no `cp`/`mv`/`git apply`/`patch` heuristics, no longer extension list (json, yaml, html, css, tf are not code): each costs false positives (2.20). Known gaps: a quoted redirect target passes; `tee` checks its first target only; `$NAME` targets stay unresolved even after `NAME=/path`: the 2.32 resolver was withdrawn when cross-vendor review reproduced wrong denies (`declare -n`, `trap … DEBUG`); it reopens only with a real shell parser.
- Explore runs on the `opus` alias under a Fable host, else inherits the session model (Claude Code docs): the explicit-model rule adds no cost.
- A session that cannot delegate (no Agent tool / `spawn_agent`) is outside the gate's design and undetectable by it — stop until the next user turn, or use the emergency override.
- Plan-marker counts and anomaly detection are field observation the owner reads (P8).
- The host runs at your session effort: start from your model generation's vendor recommendation; raise it only when accepted outcomes, cost and rework justify it.
- Tests pinning documentation sentences are how four cold audits became fixes (P7).
- Context (long conversations, cache rebuilds), not model, dominates this workload's cost. Optional, never changed by haejwo: in Claude Code lower `autoCompactWindow`, add CLAUDE.md compact instructions preserving decisions, constraints and unfinished work, start fresh for unrelated tasks, leave a handoff before long breaks; measure before using `subagentPromptCacheTtl` (costlier writes).

## Conventions
Maintainers read [`PHILOSOPHY.md`](PHILOSOPHY.md) and [`PROMPTS.md`](PROMPTS.md) first; deny strings are a tested contract.

## Verification
Run `python3 tests/test_hooks.py` and gate on the UNPIPED exit code. Live proof: Edits to 3 code files in one turn ⇒ the 3rd is denied; `/haejwo:status` shows whether hooks fire inside subagents.
