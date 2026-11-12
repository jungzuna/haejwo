# haejwo (해줘) — the cold-start plugin

haejwo keeps the main model on **judgment** and sends **execution** to worker tiers on Claude Code and Codex. Defaults: Claude Code worker tiers are Opus, Codex workers inherit the host model, the reviewer is OFF until setup verifies it.

## The 4 layers

| Layer | Artifact | What it does |
|---|---|---|
| Declaration | SessionStart hook (`session_brief.py`) | Injects the rules (a minimal core if unreadable or over budget), a setup nudge until configured, and the config summary |
| Roles | `agents/` | `deep-reasoner` (fresh context, session model and effort, never below default-worker) · `default-worker` (opus, high) · `task-worker` (opus, low) + the reviewer runner (`scripts/codex_consult.sh` on Claude, `scripts/claude_consult.sh` on Codex) |
| Criteria | `rules/orchestration.md` | When the main agent delegates |
| **Enforcement** | PreToolUse `gate.py`, `bash_guard.py`, `delegation_gate.py`; Stop `report_check.py`; PostToolUse `post_bash.py` re-presents; UserPromptSubmit `turn_reset.py` resets the per-turn counter | Main agent: budget, Bash-write and model-pin **denies**; on Claude Code a push-turn **Stop block** (Contract below) |

## Contract (3.0)
A bounded, supported version (the 2.34 contract plus the 3.0 acceptance duties, which are norms). It cannot eliminate the host's completion drive or accept-all behavior, prevent same-model reviews from being labelled independent, make broad assent authorize actions or sequences outside the enumerated list, or guarantee equivalent behavior across unhooked or unmeasured Codex host paths.

- **Enforced** (main agent; fail-open; exempt: subagents, `HAEJWO_GATE=off`, `gate.enabled` false, malformed config): the file budget (`gate.py`: edits past **N distinct code files per turn**, default 2; a path with a component in `exempt_dir_components` is not code despite a code extension (checked after the temp exemption, before the extension)); Bash code writes (`bash_guard.py`, a heuristic); the model pin (`delegation_gate.py`: general-purpose/Explore need an explicit model; unhooked on Codex); main-agent push detection (`bash_guard.py`) → Stop block ONCE (`report_check.py`; Record check below): exactly a presence check of two labels, verifying neither authorization, push success nor review adequacy. Per guard, off: `gate.bash_guard=false` (Bash code-write deny, push detection, runner/push notes), `gate.delegation_guard=false` (model pin), `gate.report_check=false` (Stop block).
- **Re-presented** only where delivery was demonstrated (notes, never denials; Notes below): runner-call note, push-returned note, Stop FACTS line. Delivery demonstrated is not benefit demonstrated.
- **3.0 acceptance duties — norms, not enforced; no demonstrated effectiveness is claimed for any of them.** After 3 consecutive review rejections on one agreed unit, the host pauses for the owner with both positions (renaming never resets the count). Briefs carry the owner's constraints verbatim, source links and supersessions; conflicting source evidence goes to the owner BEFORE a decision is asked. Acceptance reports material failed paths and test substitutions (workers report theirs) and accepts an appearance only after observing it. Reports use the user's language and vocabulary; a repeated preference is saved in the host's own memory. The record: `Final-diff review:` at every acceptance; `Outward actions:` only after an outward attempt, failed or detected attempts included with their actual result; `authorized by:` is the user's words verbatim with their time or `standing (not verified in this session): <source>`, and a disclosed standing source grants nothing. What stays enforced is listed above: on a push turn the Stop hook checks only that the two labels are present.
- **Suite runs (3.0, F5):** the host may run bounded, quiet runs of existing suites, reading the verdict from the UNPIPED exit status; suite authoring and project-exercising scripts stay delegated. Evidence: E7 only ([Measured](#measured)). Rollback signal: if a host-run suite execution misreports a verdict or truncation hides failure evidence, restore delegated execution pending re-audit.
- **Norm only** (rules, no mechanism). Selected, attributed observations, one session each, same-vendor audit: field sessions 1 (s1, 2.35.1) and 2 (s2, 2.35.2); units differ per row. Round-4 rows: [docs/field](../docs/field/2026-10-26-round-4-evidence.md).

| Obligation | Observed |
|---|---|
| Credential use stated before use | s1: 13/13 sourcing calls without prior disclosure observed (not 13 decisions); s2: 0 sourcing opportunities |
| Shared-environment side effects announced before | s2: 1/1 test-write episode not announced beforehand (authorization unresolved) |
| Security defaults disclosed | s2: 1/1 identified default not disclosed |
| Re-review of fixed diffs | s1: 1/9 material fix batches omitted, plus a separately disclosed unreviewed alias fix; s2: last fix unreviewed, risk class unresolved (no rate) |
| Per-finding judgment | s1: 21/24 findings with visible reasons/remedies (not necessarily before action) |
| Scripts and suites delegated (the 2.35 norm; 3.0 permits bounded quiet runs of existing suites) | s1 authoring: 4 host-written (1 owner-requested deliverable); s2 execution: 25 host runs (15 pytest, 10 scenario/smoke); separate units, no combined rate |

- **Measured paths:** Claude Code 2.1.305 (Stop block); 2.1.310 headless (UserPromptSubmit/PostToolUse delivery, push shapes S1/S2); 2.1.311 headless via a `--plugin-dir` checkout, not the installed cache: four 2.34.0 canaries passed 2026-10-27 (docs/field/2026-10-27-canaries-2034.md); 2.1.314 interactive, installed cache (s1): SessionStart, ordinary gate/guard/delegation paths, push-note delivery, a label-complete ending not blocked, one heuristic false deny — Stop BLOCK, deny→delegate recovery, edit-budget deny and setup unverified there; 2.1.316 interactive, installed cache (s2): SessionStart and ordinary paths, 0 denies (no deny path exercised); 2.1.317 `claude plugin eval` child (headless, isolated HOME, a copy of the 2.35.2 cache content, 1 run per arm): SessionStart injection and the edit-budget deny observed (the no-plugin arm wrote all three files), the first edit-budget observation on a real host — not interactive, not user config, no Bash or reviewer (shell grant refused on this pod). Interactive: push-note delivery demonstrated (2.1.314); runner-note delivery and Stop BLOCK unverified; setup unverified (owner waived the check 2026-10-28; a waiver is not verification). Codex host paths: unverified. Dual-host parity (historical: measured before 2.34, first stated in 2.2.0 on 2026-07-07, Codex CLI version unrecorded; not re-measured since): all but the delegation gate (Codex `spawn_agent` is unhooked), the Stop record check and the notes (silent on Codex, unmeasured) worked on both hosts, the gate apply_patch-aware; Codex tiers ride `spawn_agent` parameters (`models_codex`).
- **Everything else is unverified.** Behavior changes only on the reopen triggers ([K4](../docs/field/2026-10-27-consensus-finish.md)): "T1 — a confirmed material safety or authorization incident caused or materially enabled by the plugin, or demonstrating failure of its declared coverage; or a reproducible material regression against the frozen contract … T2 — demonstrated failure of a declared supported host/path assumption. A new upstream version alone is not demonstrated drift." Else: [`docs/field/BACKLOG.md`](../docs/field/BACKLOG.md) (reject option per item).

### Measured
Lab of 2026-11-11 on 2.35.3: offline replays of the two project E sessions (S-A, S-B), headless Claude Code 2.1.322 runs in an isolated HOME. Descriptive; no effectiveness claim.
- **Budget window:** every new prompt id restarts it, task notifications included — not a user turn. Its deny fired in none of nine field days and none of the 112 lab runs. Bash-mediated writes (`python` heredocs) are outside the counter: one field window paired one direct code edit with Python writes to two more code files.
- **Detection (E2, 2,381 main-agent Bash commands):** matched push ATTEMPTS, not executed pushes (one S-A attempt was denied for an in-place edit and never ran). Matched/missed, 2.35.3 → 2.36.0: pushes S-A 10/12 → 22/0, S-B 57/10 → 67/0 (the misses were `timeout N git push`); runner calls S-A 34/49 → 83/0, S-B 54/24 → 58/20 (the 20: after an unquoted `<<EOF` heredoc). 0 false matches.
- **File-budget pilot (E6):** 48 runs, one host version (fable-5-2 high, workers opus), tiny tasks, 3 runs per cell, reviewer off. Arms A0 no plugin, A1 rules only, A2 plugin minus budget, A3 2.35.3. All passed their hidden suites; 0 denies, 3 warnings (T3).

| Task | Mean cost (API-price equivalent), plugin arms vs A0 | Delegated code writing | Blinded cross-vendor diff review |
|---|---|---|---|
| T1 feature, 3 files | US$1.37–1.41 vs 0.42 | all plugin runs, no A0 run | critical defects: 2 of 3 A0, 0 of 9 plugin |
| T2 feature, 6 files | US$1.25–1.39 vs 0.34 | all plugin runs, no A0 run | all clean |
| T3 two-file bug | US$0.28–0.30 vs 0.22 | none (direct everywhere) | 12 byte-identical |
| T4 rename, 5 files | US$0.49–0.52 vs 0.23 | all plugin runs (`task-worker`), no A0 run | 12 byte-identical |

  With the worker catalogue available, injected rules were sufficient for the observed delegation pattern; enforcement hooks added no observed routing advantage in this pilot; plugin arms produced cleaner outcomes on the one judgment-bearing feature task at greater cost; the other three tasks showed no visible artifact-quality advantage. A2 vs A3: 0 harms in 12 pilot pairs and 32 more (E8; bound 8.9%); kept default-on (BACKLOG #20).
- **Existing-suite runs (E7, 6 pairs, 200 fast tests, a seeded failure in 3):** host-run quietly vs delegated: right verdict and failing test 6/6 vs 6/6; median host context 529 vs 2,184 tokens, US$0.18 vs 0.29, 7 s vs 25 s. Bound: bounded quiet runs of existing suites like the fixture; large or truncated output untested.

## Gate semantics

- Counts **distinct code files** (config extension list) per prompt window (every new prompt id restarts it, task notifications included; [Measured](#measured)); re-editing is free; denials name the budget and delegate, the last allowed edit warns. **Subagents are exempt** (payload `agent_id`/`agent_type`); workers may delegate further (nested agents), exempt alike from every gate and note. Turn state is per session id, sanitized and cut to 80 chars: ids alike in those chars, or absent (`unknown`), share a counter.
- **Fail-open**: any hook error or ambiguity ⇒ no objection. haejwo never approves a tool call, only denies or annotates, so your permission settings still apply. An affordance, not a security boundary (P4): editing `config.json` (not code) turns the gate off; dynamically written code is invisible to the guard.
- The temp exemption is decided by where a path RESOLVES relative to the project, not by what the file is: a path resolving under a temp dir (`/tmp`, `/var/tmp`, `tempfile.gettempdir()`) outside the active project (git toplevel, else cwd) is not code, scratch code included; a temp-dir repository that IS the project is gated unless its root is a temp dir or ancestor (`/tmp`, `/`). In-place editors run via `find`/`xargs` are denied on any path.
- **Record check (Stop, 2.32):** after the bash guard passed a main-agent `git push` this turn (noted to the model, recorded as `outward-attempt`), a final message lacking `Outward actions:` or `Final-diff review:` is blocked ONCE naming both; `not run because:` satisfies the review label. Presence only. Exempt: `HAEJWO_GATE=off`, `gate.enabled`/`gate.report_check` false, malformed config, subagents, Codex hosts (unmeasured), `stop_hook_active`, a prior nudge, a failed state write. Detection is a grammar, not the shell: dry-runs (`-n`, `--dry-run`) and `--help`/`-h` are not attempts; `timeout N` is skipped like `env` (2.36.0); quoted-delimiter heredoc bodies are skipped, what follows scanned. Missed: unquoted-delimiter bodies and what follows them, continued headers, `$(…)` in bodies, unlisted wrappers (`sudo git push`). A turn idle past the 2 h stale reset can be nudged twice. Measured on Claude Code 2.1.305: the block shows as a "Stop hook error", then the model wrote both lines. Acceptance reports without a push are unchecked (not countable).
- **Notes (2.34):** each pairs ONE existing obligation with event facts, from `post_bash.py` only. Per detected runner call: `[haejwo] detected runner call ({runner}; tool call {id}): command syntax only — whether it ran, finished or reviewed anything is not established.`, then the reply-reading obligation (per-finding fact vs remedy, the closing statement, same-model ≠ independent). One per tool call id; dedupe keeps the newest 64 ids (an evicted id replayed emits again); a setup probe (an `HJW_PROBE=` assignment word before the runner's command word) gets none. After a detected push-bearing Bash call returned: tool call id; quoted shell output tail (the last line non-empty after sanitizing, non-printables removed, URL credentials redacted, 160 chars). The Stop block reason adds a FACTS line: the count and tool call ids of detected push-bearing calls. Detected command text, execution and remote outcome are three things: `false && git push` is detected and may never run; a returned call is not a successful push; no hook classifies output. Bound per session ≈ runner calls × ≤ 400 + push calls × ≤ 390 + Stop blocks × ≤ 690 chars (3.0.0: the two authorization forms), measured with 30-char tool-call ids and 2-digit counts; longer ids lengthen the line proportionally (ids are not truncated); each emission logged with its length; a note needs its dedupe claim saved first (lock busy or save failed: no note). `gate.bash_guard` gates `post_bash.py`; `gate.turn_checklist` is gone (an old key is kept, unused). Exempt: as Enforced, plus Codex hosts (unmeasured). `bash`/`sh <runner>` counts since 2.36.0. Missed: `bash -c '…consult.sh'`, `bash -x`/`/bin/bash <runner>`, a runner after an unquoted `<<EOF` heredoc, a runner path held in a shell variable (`S=…; $S -o …`), failed tool calls (PostToolUseFailure unmeasured: no note); push-bearing call ids beyond 64 per session get no return note and no dedupe.
- **Measured host facts** (measured on Claude Code 2.1.310 headless; roles per the CLI transcript): UserPromptSubmit and PostToolUse context reach the model (UserPromptSubmit's persists into later turns); PreToolUse notes land after the tool ran, with PostToolUse's, so hook notes report and prevent nothing; any Stop output costs one extra model turn; SubagentStop output goes to the subagent and REPLACES its answer, so haejwo never uses it. Measurements are per version.

## First run
**`/haejwo:setup`** is optional (nudged until configured), once per machine/account; config (`~/.claude/plugins/data/haejwo-haejwo/config.json`; Codex `~/.codex/plugins/data/haejwo-haejwo/`) lives outside the plugin cache, so it survives updates. Its helper runs preflight, one round (Custom adds a follow-up; an inconclusive sandbox failing its read-only probe twice adds a second; not logged in: log in, re-run), write+verify (≈ 30 s authenticated, measured 2026-10-27). A re-run offers Keep only while reviewer CLI version, login and settings are unchanged (fingerprint) and verification is under 30 days old (status and the session brief flag older). Read-only is tried only where `codex sandbox -- true` works or is inconclusive; a recognised launcher failure goes to one-time `danger-full-access` consent (full disclosure, remembered until revoked, shown by `/haejwo:status`). On Claude Code it runs on Sonnet until the next prompt (a resume may keep Sonnet). Before setup, defaults apply (gate ON, 2 files/turn, bash-guard ON) unless `/haejwo:gate` stored values. A Claude Code worker runs at its agent file's model unless one is passed; the gate steers an omitted override missing a configured pin, never verifying which model ran.

## Commands
Normal use needs **none**.

| Command | Role |
|---|---|
| `/haejwo:plan <topic>` | Reviewer critique → host-decided plan; feature-scale briefs embed it (`Plan:`) |
| `/haejwo:setup` | Configuration + reviewer probe |
| `/haejwo:status` | Config, turn counter, reviewer readiness, observations, delegations (read-only) |
| `/haejwo:gate [on\|off\|N\|bash on\|bash off]` | Emergency hatch / live tuning |

Gate fires log to `state/observations.jsonl` (subagent-exempt allows as per-interval counts since 2.36.0; status prints the retained interval); `HAEJWO_GATE=off` works only in the host's environment (no per-command bypass).

## Install
See the [root README](../README.md); Codex in headless CI only: `--dangerously-bypass-hook-trust`. `/reload-plugins` (Claude Code) refreshes hooks, commands and agents, not the injected rules — restart after install/update.

**Support floor:** runners support installs >= 2.22.0; a 2.26+ runner never forwards below it (says so, runs locally). Pre-floor installs are unsupported as sources too: without the `snapshot.py` tombstone, a pre-floor runner stays local.

## Reviewer runners
**Usage:** `<runner> [-o out.md] brief.md`, or `echo … | <runner> -` (stdin brief, deleted on exit), from the project root. Without `-o` the reply is `<brief-stem>.reply.md` (`plan.md` → `plan.reply.md`), the log beside it; keep replies and briefs until the acceptance is reported (cleanup must not delete them). For a stable tree, pause writes or review a prepared worktree.

Exit codes, env knobs and config keys, and the official `codex@openai-codex` plugin note: [docs/runners.md](../docs/runners.md).

**Guarantees**
- A standing non-editing REVIEWER CONTRACT is prepended to every brief and asks for a closing statement (material read, verification run, omissions), a provenance label per finding (regression / base / unresolved), a flag on remedies beyond the brief or plan, and for a blocking finding the agreed criterion it violates and the minimum remedy, optional hardening listed apart: the reviewer's claims, unverified, neither blocking nor waiving anything; `claude_consult.sh` also disallows Edit/Write/NotebookEdit.
- Post-run change detection FAILS the run when the repository changed: HEAD, tracked files, untracked files (contents for the first 2000). Runner artifacts are excluded.
- **Artifact guard:** a reply, log, events or temp-brief path inside the worktree or its git dirs (lexically or via a symlink), or an existing hard-linked artifact, is refused (exit 2) before any write or paid call. Other worktrees of the repository are not covered.
- **Receipt (3.0):** a returned reply file starts with runner facts: repository (change detection's root), base (`HJW_REVIEW_BASE` verbatim, labelled caller-supplied and not verified, else `unavailable`), HEAD, the schema and SHA-256 digest of change detection's BEFORE snapshot, and the line "A snapshot identifies the observed state, not reviewer coverage. No inheritance through a merge." Outside git: `unavailable`. Nothing is stored, compared or parsed for a verdict; a failed run writes none, and a receipt that cannot be written fails the run. Only the leading block of a successful run has runner provenance; a receipt-like block elsewhere is reviewer text.
- A codex model rejected before execution fails with one hint, unretried.
- Fail-closed: a git error, unreadable snapshot or timeout fails the run (before the paid call when the BEFORE snapshot fails); unreadable files are disclosed (`some files unreadable: N`).
- Python helpers and the reviewer CLI run under a python3 wall clock; the bootstrap python, coreutils, log scans, file copies and stdin-brief reads do not. Model and effort print with their source.

**Not guaranteed**
- **Not a security boundary:** Bash stays available to the reviewer (tests, greps).
- Never covered: package installs, MCP/user/global config, ignored or out-of-repo files, edit-then-restore. Under concurrent writers a change has **attribution unknown**.
- **Review coverage is what the reviewer read.** Without repo access it sees only the brief; a scope-limited review names its scope and omissions. The codex result line's `shell: N completed commands` does not establish coverage; `file access unverified (0 shell commands)` means file access is UNVERIFIED, not that no files were read; the header's `scope=` (`scope=none (change detection off)` outside git) is the root change detection covers, not what the reviewer read.
- A failure reported only in prose is NOT detected (measured 2026-09-21); the host reads every reply. `claude_consult.sh` has no event classifier.
- An unselected model is **`cli-default (identity unverified)`**; the Codex-host workflow is field-untested.
- The `codex` block names the reviewer of the data dir's host — on a Codex host `codex.model` is a Claude model; a rename is deferred as schema debt.

## By design
Rejected recurring objections:
- The budget applies despite same-model workers: it caps code files edited per prompt window (allowed attempts by extension, not successful edits: one hook; Bash-mediated writes are outside it), not context size or model price.
- Runners do not check `enabled`/`verified_at`: the host is the policy gate, the runner a tool.
- A stored `danger-full-access` consent persists; every run header discloses it.
- Stale cache-path runners forward to the installed version (Claude Code only): restarting pushes a measured host failure onto users; the support floor bounds the cost.
- The runner stack keeps the artifact guard and change detection: plain status/diff checks omit their contracts.
- The bash guard covers redirects, `tee` and in-place editors only — no `cp`/`mv`/`git apply`/`patch` heuristics, no longer extension list (json, yaml, html, css, tf are not code): each costs false positives (2.20). Known gaps: a quoted redirect target passes; `tee` checks its first target only; `$NAME` targets stay unresolved even after `NAME=/path`: the 2.32 resolver was withdrawn when cross-vendor review reproduced wrong denies (`declare -n`, `trap … DEBUG`); it reopens only with a real shell parser.
- Explore runs on the `opus` alias under a Fable host, else inherits the session model (Claude Code docs): the explicit-model rule adds no cost.
- A session that cannot delegate (no Agent tool / `spawn_agent`) is outside the gate's design and undetectable by it: stop until the next user turn, or use the emergency override.
- The host runs at your session effort: start from your model generation's vendor recommendation; raise it only when accepted outcomes, cost and rework justify it.
- Context (long conversations, cache rebuilds), not model, dominates this workload's cost. Optional, never changed by haejwo: in Claude Code lower `autoCompactWindow`, add CLAUDE.md compact instructions preserving decisions, constraints and unfinished work, start fresh for unrelated tasks, leave a handoff before long breaks; measure before using `subagentPromptCacheTtl` (costlier writes).

## Conventions
Maintainers read [`PHILOSOPHY.md`](PHILOSOPHY.md) and [`PROMPTS.md`](PROMPTS.md) first; deny strings are a tested contract.

## Verification
Run `python3 tests/test_hooks.py` and gate on the UNPIPED exit code. Live proof: Edits to 3 code files in one turn ⇒ the 3rd is denied; `/haejwo:status` shows whether hooks fire inside subagents.
