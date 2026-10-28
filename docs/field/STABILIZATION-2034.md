# Stabilization ledger — haejwo 2.34.0 → 2.35.1 (K5)

One stabilization attempt, no automatic extension (debate 6 K5,
[2026-10-27-consensus-finish.md](2026-10-27-consensus-finish.md)). Missing exposure is
"unverified", never "passed". Two field sessions are an operational checkpoint, not
statistical validation.

## Budget
| Item | Value |
|---|---|
| Start | 2026-10-27 10:37 KST (plan critique started) |
| Cap | 5 maintainer working days + 2 field sessions on different projects |
| Cumulative maintainer time | 2026-10-27 10:37 → 13:31 KST (release candidate 11:12 after plan critique, two workers + harness worker, diff review, fix round, canaries ×2, re-review; 2.34.0 committed 11:31, 2.35.0 13:31) + 2026-10-28 09:20 → 10:10 KST (documentation audit + 2.35.1); 2 of 5 maintainer days consumed (calendar days touched). The debate itself (2026-10-27, before 10:37) is not counted |
| Field sessions | 0/2 |
| Scope | debate 6 K3 exactly (removals, retained notes, wording, contract, this folder) + the owner amendment (setup helper, 2.35.0; below) + the 2.35.1 documentation patch |

## Retained claims and the exposure each needs
| Claim | Mechanism | Exposure needed | Status |
|---|---|---|---|
| <= N code files per turn for the main agent | `gate.py` | tests + canary edit | tests pass (suite 1,439 at 2.35.0; 1,346 at 2.34.0); canary edit: unverified (no edit canary was run) |
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
- 2.34.0: suite `python3 tests/test_hooks.py` unpiped, 1,346 passed, 0 failed, exit 0 (host run
  after the final fix; 1,320 at 2.33.0).

## Field sessions
| # | Project | Date | Host version | Claims exercised | Material contract failures |
|---|---|---|---|---|---|
| 1 | _pending_ | | | | |
| 2 | _pending_ | | | | |

## Disclosures
- Plugin README word cap: re-pinned to the shipped count rounded up to 10 — 2,249 words
  before 2.34.0, 2,571 at the release candidate, cap 2,580. The growth is the Contract
  section (K2); the 2.33 pilot paragraph removal paid for part of it.
- rules/orchestration.md: byte-identical (3,744 B; ratchet 3,750).
- D11 (budget across hand-back turns): CLOSED, not adopted.
- Known, pre-existing parser limit: `env -iC d …` short clusters with an operand are not
  understood by the shared command-word walk (no note either way); unchanged because it
  shares the push grammar.

## Owner amendment (2026-10-27, before the field sessions)
The owner reopened ONE item outside T1/T2, as the owner's own decision (the owner sits
above the constitution; P2 cost): the setup experience — too many model turns and
question rounds on a large session model, and a reviewer connection that always
attempts a read-only sandbox this machine cannot provide before asking for
`danger-full-access`. Scope: a deterministic setup helper (preflight / write / verify),
one question round, remembered consent asked once with the full disclosure, no new
enforcement, no change to the reviewer runners. Shipped locally as 2.35.0 (5be0ab2); the
two field sessions run it (2.35.1 docs patch on top); the K5 budget (5 maintainer days, 2 sessions) is unchanged and
this work counts against it. Rejection alternative recorded: keep the current flow and
only document that setup is once per machine.

### 2.35.0 plan
- Codex plan critique (one round, 14 objections): 14/14 accepted, none rejected or deferred.
- Measured on this pod (2026-10-27, Claude Code 2.1.311, codex-cli 0.163.0): the read-only
  preflight (`codex sandbox -- true`) fails in 36 ms with the bwrap launcher line; reviewer
  smoke 8 s; repo-read probe 18 s. The command frontmatter `model: sonnet` switch was
  measured headless only: the command's turn ran on Sonnet, the session model returned on
  the next prompt, a headless `--resume` kept Sonnet; AskUserQuestion is unavailable
  headless, so its behaviour under the override is unverified.
- Candidate live run (host-run by the code worker on this pod against a TEMP config path,
  never the real one; deterministic parts only): `preflight` → `broken` with the bwrap
  launcher line (36 ms class); `verify --sandbox danger-full-access --consented` → verified
  in 21.1 s total (smoke 8.5 s, repo-read 12.5 s); the runner log's `config=<path>` header
  check passed; artefacts under `<config dir>/setup/` with mode 700; no temp repository left
  behind. The helper also records `verification_pending` and a fingerprint that includes the
  reviewer model, effort and verified sandbox. Host re-run on the FINAL helper (after the
  three blocker fixes), same temp-config method: preflight `broken`, `write` ok, `verify
  --sandbox danger-full-access --consented` verified in 19 s, `keep-check` → keep=true
  "configured; reviewer verified", `verification_pending` cleared, setup dir mode 700, no
  temp repository left. Launching the helper from `/tmp` itself is refused by design
  (stage temp-repo: "temp dir is under the cwd"). This is evidence for the helper, not for
  the interactive flow.
- Review trail 2.35.0: Codex diff review — 3 blockers (timeout did not reap bounded.py's
  own-session child; temp-repo cleanup gap before `realpath`; non-launcher permission errors
  classified `broken`), 6 should, 1 nit; four fixed-diff re-reviews, the last three on the
  preflight classifier (abbreviated/quoted paths, then payload-exec lines), round 4 RESOLVED
  with a recorded non-blocking residual: launcher prefixes and stage keywords are a heuristic,
  accepted under the cost asymmetry (a wrong `broken` = one declinable consent question with
  the full disclosure; a wrong `unknown` = the ordinary read-only attempt and retry). Final
  rule: a line starting with a launcher prefix whose remainder names a sandbox-initialization
  stage; payload-exec lines never count. Suite 1,439 passed, 0 failed, exit 0 (host run).
- Gate for pushing 2.35.0: the owner's interactive pre-ship check in a fresh session
  (`--plugin-dir`) — the question round, the consent flow, AskUserQuestion under the
  Sonnet override, and `/model` after the command. Until it passes, 2.35.0 stays local.
  WAIVED 2026-10-28 by the owner's words ("테스트는 필요 없어" — the test is not needed): the
  interactive check was not run, so the interactive setup flow stays unverified; 2.35.0 and
  2.35.1 can be pushed on the owner's say-so. The gate's definition is otherwise unchanged.

### 2.35.1 (documentation consistency, 2026-10-28)
- Three read-only audits at 5be0ab2 — the READMEs (root, Korean, plugin); the internal
  LLM-facing docs (PROMPTS, PHILOSOPHY, rules, commands, agents); this folder — 49 rows:
  wrong 3, stale 7, duplicate 1, missing 9, nit 29. Zero code, deny-string, hook-contract or
  version mismatches.
- Applied (documentation only, nothing under `haejwo/scripts/`): plugin README (the
  plan-marker record is outside `gate.delegation_guard`; `turn_reset.py` named; config path
  outside the plugin cache; setup rounds; the 30-day and fingerprint Keep rule; runner exit-2
  and reply-name nits); root and Korean READMEs (the push-turn Stop block sentence,
  `bash on`/`bash off`, Korean drift: plan trigger, CI line, the By-design pointer mirrored);
  PROMPTS (`model:` key, `setup_probe.py` strings in Scope, prefixes, pins in `tests/r3_a.py`,
  README Contract = full mechanism catalogue); `default-worker` Report back ends with
  `Judgment calls:`; `commands/gate.md` (writes config, no 1-10 ceiling), `setup.md`
  (`verify_fingerprint`, every removed reviewer key, Codex effort default, `verify` without
  `--effort`), `status.md` (retired 2.33.0 hook names); this folder (ledger, index, backlog).
- Deferred to [BACKLOG.md](BACKLOG.md), each with a reject option: status showing
  `verification_pending` (#8) and status calling the setup preflight (#10) — behaviour;
  the setup helper's `--mode consult` (#9) — code; the PHILOSOPHY P7 origin clause and the
  Docs-map rows (#11) — K8 keeps PHILOSOPHY unchanged. `rules/orchestration.md` untouched
  (3,744/3,750 B): PROMPTS now says the README is the catalogue and the rules name the
  subset the host acts on. Korean README badges: omitted deliberately. The README T1 ellipsis
  stays (explicit elision).
- Word caps (`tests/test_hooks.py`): plugin README sat at exactly 2,580 = cap before this
  patch; re-pinned 2,580 → 2,620 (shipped 2,620, after trimming duplicated wording); root
  README re-pinned 740 → 770 (shipped 768). Unchanged caps: `README.ko.md` 650 (547),
  `commands/setup.md` 900 (900, trimmed to fit), `commands/status.md` 310 (310, trimmed to
  fit), `commands/plan.md` 560 (553). The cap comment's "2,577" is corrected to 2,571 (the
  606d143 count). The rules byte ratchet and the runner-stack line ratchet are unchanged.
- Host note: this pod now runs Claude Code 2.1.312, outside the measured 2.1.305/310/311
  matrix; under K4 a new upstream version alone is not drift. Field-session rows must record
  the host version.
- Suite: the worker's run showed 1,437 passed, 2 failed (the 2.35.0 version pin at
  `tests/r4_a.py:180`, outside its file list); the host moved the pin to 2.35.1 and re-ran
  `python3 tests/test_hooks.py` unpiped: 1,439 passed, 0 failed, exit 0. `tests/mirrors.py`:
  no drift.

## Closure conditions
- Acceptance requires zero unresolved material contract failures; a claim without its exposure is reported "unverified".
- At the cap: remove the unsupported claims or features, or withhold the release. No extension.
- After acceptance, scheduled monitoring and debates END; reopen only under T1/T2; everything else goes to [BACKLOG.md](BACKLOG.md).
