# Stabilization ledger — haejwo 2.34.0 (K5)

One stabilization attempt, no automatic extension (debate 6 K5,
[2026-10-27-consensus-finish.md](2026-10-27-consensus-finish.md)). Missing exposure is
"unverified", never "passed". Two field sessions are an operational checkpoint, not
statistical validation.

## Budget
| Item | Value |
|---|---|
| Start | 2026-10-27 10:37 KST (plan critique started) |
| Cap | 5 maintainer working days + 2 field sessions on different projects |
| Cumulative maintainer time | 2026-10-27 10:37 → 11:12 KST at the release candidate (plan critique, two workers + harness worker, diff review, fix round, canaries ×2, re-review); the debate itself (same morning, before 10:37) is not counted |
| Field sessions | 0/2 |
| Scope | debate 6 K3 exactly (removals, retained notes, wording, contract, this folder) |

## Retained claims and the exposure each needs
| Claim | Mechanism | Exposure needed | Status |
|---|---|---|---|
| <= N code files per turn for the main agent | `gate.py` | tests + canary edit | tests pass (suite 1,317); canary edit: unverified (no edit canary was run) |
| Bash code writes denied (heuristic) | `bash_guard.py` | tests (deny contract) | tests pass |
| Generic agents need an explicit model; tier pin | `delegation_gate.py` | tests + canary Agent | tests pass; canary 2: delegation record present, no errors |
| Plan-marker presence recorded | `delegation_gate.py` | tests | tests pass |
| Main-agent push detected → note → `outward-attempt` → Stop block once | `bash_guard.py` + `report_check.py` | tests + canary (4); field: unverified until a push turn occurs | tests pass; canary 4 pass; field: unverified |
| Runner-call note with its limits; setup probe excluded | `post_bash.py` | tests + canary (3) | tests pass (15 parser cases incl. option-operand spellings); canary 3 pass |
| Push-returned note | `post_bash.py` | tests + canary (4) | tests pass; canary 4 pass |
| Session brief within 5,000 chars on the worst combination | `session_brief.py` | tests (fixture) | tests pass |
| Norm-only obligations | rules | none (reported rates only) | reported, not verified |

## Installed-host canaries
Claude Code 2.1.311, headless, `--plugin-dir` checkout (not the installed cache).
Full hook sequences: [2026-10-27-canaries-2034.md](2026-10-27-canaries-2034.md).

| # | Canary | Expected | Result |
|---|---|---|---|
| 1 | A user prompt | no `[haejwo turn]` context | PASS (UserPromptSubmit output empty) |
| 2 | An Agent launch | no hook error, delegation record present, no note | PASS |
| 3 | A runner call; the setup probe (`HJW_PROBE=1`) | one note with the 2.34.0 text; none for the probe | PASS (note 380 chars; probe: PostToolUse empty) |
| 4 | Push shape `true \|\| git push origin HEAD` (detected, never executed) | push note, `outward-attempt`, Stop block once with FACTS | PASS (block once, FACTS id count 1, record lines then accepted) |

Run twice (before and after the probe-parser fix from the diff review); identical outcomes.
Not demonstrated: interactive mode, installed-cache path, a real push, reviewer
execution, any behavioural benefit.

## Review trail
- Plan critique (Codex, high): 14 objections, 14 accepted, 0 rejected, 0 deferred.
- Diff review (Codex, high): 1 blocker (probe marker counted `env` option operands as
  assignments), 3 should, 1 nit — all fixed.
- Fixed-diff re-reviews (Codex): three rounds on the probe-marker parser — round 1 found
  abbreviated `env` long options (`--chd`), round 2 found split-string expansion
  (`--spl=-C`), round 3 RESOLVED with no new blocker (263 additional spellings checked by
  the reviewer). Rule adopted: any split-string spelling makes the whole segment
  non-probe (a kept note is the safe failure).
- Suite: `python3 tests/test_hooks.py` unpiped, 1,346 passed, 0 failed, exit 0 (host run
  after the final fix; 1,320 at 2.33.0).

## Field sessions
| # | Project | Date | Claims exercised | Material contract failures |
|---|---|---|---|---|
| 1 | _pending_ | | | |
| 2 | _pending_ | | | |

## Disclosures
- Plugin README word cap: re-pinned to the shipped count rounded up to 10 — 2,249 words
  before 2.34.0, 2,571 at the release candidate, cap 2,580. The growth is the Contract
  section (K2); the 2.33 pilot paragraph removal paid for part of it.
- rules/orchestration.md: byte-identical (3,744 B; ratchet 3,750).
- D11 (budget across hand-back turns): CLOSED, not adopted.
- Known, pre-existing parser limit: `env -iC d …` short clusters with an operand are not
  understood by the shared command-word walk (no note either way); unchanged because it
  shares the push grammar.

## Closure conditions
- Acceptance requires zero unresolved material contract failures; a claim without its exposure is reported "unverified".
- At the cap: remove the unsupported claims or features, or withhold the release. No extension.
- After acceptance, scheduled monitoring and debates END; reopen only under T1/T2; everything else goes to [BACKLOG.md](BACKLOG.md).
