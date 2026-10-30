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
| Cumulative maintainer time | 2026-10-27 10:37 → 13:31 KST (release candidate 11:12 after plan critique, two workers + harness worker, diff review, fix round, canaries ×2, re-review; 2.34.0 committed 11:31, 2.35.0 13:31) + 2026-10-28 09:20 → 10:10 KST (documentation audit + 2.35.1) + 2026-10-30 11:06 → 14:30 KST (field session 1 monitored live from the haejwo maintainer session — transcript + observation records; the deny reproduction; the two-round evaluation debate with Codex and the read-only review grading; the one-round improvement debate: no code change); 3 of 5 maintainer days consumed (calendar days touched). The debate itself (2026-10-27, before 10:37) is not counted |
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
| 1 | project M (a three-service web application, stages 1–2; 2 user prompts, 7 delegations, 3 Codex diff-review threads, commits only) | 2026-10-30 11:06 → 13:14 KST | Claude Code 2.1.314 (outside the measured 2.1.305/310/311 matrix; K4: not drift by itself), haejwo 2.35.1 from the installed cache, session model Opus 5.5 | gate: 1 direct code edit allowed (`ok`), 10 temp-exempt, 3 non-code, 2 metadata-exempt, 0 denies; bash guard: 84 `ok`, 13 `unresolved-target`, 0 denies (sub-agents: 419 exempt); delegation: 7 allowed, tier pin `pass:pin-matches-default` ×7, plan marker `no_plan` ×1 / `none` ×6 (the six briefs carried `Plan: design doc §…` in brief FILES the prompt only pointed at — presence-only record, by design); runner note: 0 emitted for 14 runner invocations — 1 `--help` (excluded by design), 13 graded review runs with the runner path in a shell variable (`$S -o …`; the documented unresolved-`$NAME` limit, 2.32 resolver withdrawn); push path EXERCISED (second half of the session: authorized 13:25 KST, pushes 13:59–14:00 KST, after the owner's written authorization to commit, push and copy to the deploy side): 2 `git push` calls → 2 `outward-attempt` records, 2 PreToolUse push notes and 2 PostToolUse push-returned notes (232 chars, two `main -> main` push tails quoted by the note) all delivered as `hook_additional_context` attachments on 2.1.314 interactive from the installed cache; the final message carried `Final-diff review:` + a multi-line `Outward actions:` block quoting the owner's words at the time → Stop check passed, no block (the pass path leaves no record); the earlier non-push turns ended with `Outward actions: none - authorized by: none`, and a platform-folder copy turn carried its own line; Stop BLOCK path still unexercised; session brief delivered (attachment record, `[haejwo config]` line); setup not run (config pre-existing, no nudge) — the interactive setup flow stays unverified | **none.** One wrong deny, below the reopen bar: the bash guard denied a `python3 - <<'EOF' … EOF; sed -i … MEMORY.md` command (`via=inplace`, targets taken from a Python string in the heredoc body because a `;` inside the string split the segment — the real `sed -i` target was a Markdown memory file); the host recovered with the Edit tool in 5.4 s. Reproduced offline byte-for-byte at 2.31.0, 2.32.0, 2.33.0, 2.34.0 and 2.35.1 (not a regression; the write scan has never skipped heredoc bodies — the quoted-heredoc skip belongs to the push and runner matchers; declared "heuristic"; the 2.20 docstring defers heredoc-body cases; the one-line fix flips 7 of 10 pinned adversarial tests in the same-vendor reproduction and 6 of 10 in Codex's — the 2.26 signed stop condition either way) → BACKLOG #14. Norm-only observations (no mechanism claims them): `.env` sourcing by the host 13× (`set -a; . …/.env`) with 0/13 disclosures; review handling (read-only same-vendor grading): 13 rounds, 24 material findings → 21 fixed / 3 rejected with a reason / 0 deferred, reasoned decision visible 21/24, fixed-diff re-review 8/9, reviewer omissions carried into the host's reports 0/2; counts reconciled across all four observation rotations at the 14:04 KST snapshot (every host call has a record). Evaluation consensus: [2026-10-30-consensus-field-1.md](2026-10-30-consensus-field-1.md) (F1–F8) (keys listed by name only, no values printed); host-written project-exercising script 1 (the scratchpad deploy script the user asked for as a deliverable); a Claude Code auto-mode classifier denial ("shared resource change") was respected, not re-routed (1/1); outward scope kept (push only on the user's separate word, none requested) |
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

### 2.35.2 (privacy redaction + lean READMEs, 2026-10-30)
- Owner request, documentation/comments/one test fixture only; no behaviour, hook, deny-string
  or code change. A read-only privacy audit of every tracked file (50 rows: 22 redact, 28 keep)
  and a read-only README pin audit preceded it.
- Redacted at HEAD: 34 client/project identifier occurrences in 14 tracked files (32 project
  labels, 1 tracker task id, 1 route prefix) replaced with neutral labels (`project M`,
  `project K`, `project E`, `a demo app`, `smoke`; the fixture's route pair in
  `tests/r2_a.py` became `/v1_old/* → /v1/*`, behaviour unchanged); in the field-session-1 row
  above, 1 verbatim quote of the owner's words paraphrased, 2 push hash ranges of the client
  repository dropped and 1 client feature/component description neutralized (every count and
  claim kept). Comment-only edits in three runner/guard scripts kept their line counts (the
  runner-stack line ratchet stays at 1,830). `.gitignore` gains `.env` and `.env.*` (nothing
  was ever tracked). Consensus files in this folder took the same label map (see
  [README.md](README.md)); session-id prefixes are unchanged.
- Git history still carries the old labels (earliest appearances 2026-09-29 to 2026-10-30, per
  the audit); no history rewrite was done.
- READMEs (words, `str.split()`): root 768 → 450 (cap 770 → 450), Korean 547 → 318 (cap
  650 → 320), plugin 2,620 → 2,402 (cap 2,620 → 2,410). The host-combination table and the
  non-goals moved to `docs/non-goals.md`; the runner exit codes, knobs/config and the
  `codex@openai-codex` note moved verbatim to `docs/runners.md`; Dual-host parity folded into
  the Contract's Measured paths line; two By-design bullets without a test or K statement
  dropped (documentation-pinning tests, plan-marker counts). The Contract (apart from the
  folded parity sentence, which replaced its "(Codex gate parity: Install)" pointer), the Record
  check and Notes paragraphs and the three tested substrings are unchanged.
- The documentation-only acceptance patch that F8 and the improvement debate call "2.35.2"
  ships as 2.35.3.

## Closure conditions
- Acceptance requires zero unresolved material contract failures; a claim without its exposure is reported "unverified".
- At the cap: remove the unsupported claims or features, or withhold the release. No extension.
- After acceptance, scheduled monitoring and debates END; reopen only under T1/T2; everything else goes to [BACKLOG.md](BACKLOG.md).
