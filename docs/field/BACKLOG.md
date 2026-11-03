# Backlog — mechanisms not built

Source: debate 6 K3 ("Not built → backlog with an explicit reject option") and K4
([2026-10-27-consensus-finish.md](2026-10-27-consensus-finish.md)). These items never
reopen a release on their own: only T1 or T2 does, verbatim from K4:

> T1 — a confirmed material safety or authorization incident caused or materially
> enabled by the plugin, or demonstrating failure of its declared coverage; or a
> reproducible material regression against the frozen contract, including misleading
> evidence (wrong counters/records), permission interference, or loss of required
> review artifacts.
> T2 — demonstrated failure of a declared supported host/path assumption. A new
> upstream version alone is not demonstrated drift.

The backlog is reviewed at most quarterly. Declining an item is a recorded outcome
(K6): choosing `Reject:` moves it to `## Closed` with the date and the reason.

Each item: what it would do, why it was not built, the evidence that would reopen it,
and the options.

## 1. Missing-marker note
- Would: a nonblocking note on a delegation whose brief lacks `Plan:` / `No plan because:` (respecting read-only and mechanical exemptions).
- Not built: a new delivery assumption and obligation; the marker's PRESENCE is already recorded (a record, not adequacy).
- Reopen evidence: a T1 incident traced to an unplanned feature-scale brief, or a field audit with a predeclared denominator showing missing markers on risk-classed briefs that led to material rework.
- Options: Build (bounded, measured delivery) · Defer · `Reject:` remove the item; the presence record stays the only mechanism.

## 2. Acceptance-record reminder
- Would: a compact reminder of both record labels (`Final-diff review:` / `Outward actions:`) at the detected acceptance point, no Stop block.
- Not built: its delivery point was the hand-back turn, which async launches never reached in 2.33.0; the push-turn Stop check already covers the outward case.
- Reopen evidence: a T1 incident where an acceptance without a review record enabled a material defect, plus a measured delivery point on the installed host.
- Options: Build · Defer · `Reject:` remove the item; the rules' template stays norm-only for non-push acceptances.

## 3. Hand-back detector (debate-5 C2(a))
- Would: detect worker hand-backs on UserPromptSubmit from the measured envelopes, deduplicated across channels.
- Not built: its only consumer was the removed turn checklist; mid-turn notifications without UserPromptSubmit stay invisible; correlation needs a hand-back identity across resumes (unproven).
- Reopen evidence: a retained mechanism that needs hand-back facts, plus a measured identity that survives resumes and duplicate channels.
- Options: Build · Defer · `Reject:` remove the item.

## 4. Completion ledger
- Would: record which reviewer run covered which change, so a review count means reviewed changes.
- Not built: needs a change identity and failure semantics that are not specified.
- Reopen evidence: a T1 incident caused by a misleading review count or record (K6), or a specified change identity with a measured failure mode.
- Options: Build · Defer · `Reject:` remove the item; the runner note keeps stating only "detected runner call".

## 5. Round detector
- Would: notice review/fix rounds that repeat without convergence.
- Not built: no countable definition of a round; debate 5 dropped it (C4).
- Reopen evidence: a countable round definition and a field case where a non-converging loop caused material cost the user did not authorize.
- Field evidence (session 2, project E, 2026-11-02; [consensus](2026-11-03-consensus-field-2.md) H4):
  - Ten diff-review rounds: reviewer verdict reject in rounds 1–9, about 6 h with no owner checkpoint.
  - A same-vendor classification of the stored replies found 57 findings, 35 of them blocking. It is not independent, and nothing was re-run.
    - By class: gaps in the previous round's fix 34; already present in the base 9; beyond the brief or plan 6; regressions 4; non-actionable 4; invented 0.
    - Blocking findings worse than the base: 11, all in rounds 1–6. Rounds 7–10: 0.
  - From round 3 the host tightened its own acceptance criterion beyond the plan without owner input. A redesign departing from the plan would first have tripped a "departs from the reviewed plan" trigger at round 3.
  - The estimate that the loop could have stopped after round 7 is SPECULATION.
- External material ([debate 10](2026-11-03-consensus-external-skills.md); all labelled, none measured for this purpose):
  - For a cap: addyosmani/agent-skills "3 cycles, escalate to the user" (unmeasured; its eval does not test it). mattpocock/skills has a cap in an unshipped skill.
  - Against a cap: pstack's unlinked anecdote of a real defect on review pass 7 after every earlier pass was fixed. mattpocock/skills separately rejects numeric question caps.
- Candidate policy (not implemented; host wording, after Codex's suggestion):
  - (a) A fix that changes an agreed design decision revisits the plan before implementation.
  - (b) After K consecutive diff-review rounds with reviewer verdict reject, pause automatic review and return to the owner with both positions and their evidence.
  - K is a cost policy. Reaching it establishes neither correctness nor permission to ship.
  - Host change to Codex's draft: the draft counted only rounds whose findings were all *rejected* by the host, which would not fire on session 2, where every finding was accepted.
  - Related, adopted in 2.35.3: the reviewer now labels provenance and flags beyond-brief remedies (#19), which exposes (a) without enforcing it.
- Options: Build (one rules sentence; it must displace bytes, since the rules are at 3,744/3,750) · Defer · `Reject:` remove the item; the existing added-scope disclosure rule stays the only lever.

## 6. Interpreter reminders
- Would: re-present the scratch-delegation obligation on main-agent inline interpreter calls (`python -c`, `python - <<EOF`).
- Not built: most inline runs are eligible direct work (setup, config, narrow reads); a blanket reminder is noise (debate 5 D9).
- Reopen evidence: a T1 incident caused by host-run project-exercising code on shared infrastructure, plus a predicate separating such code from reads, measured for false positives.
- Options: Build · Defer · `Reject:` remove the item; obligation 6 stays norm-only.

## 7. Credential detector
- Would: record credential syntax classes (never values) in main-agent Bash and note writes to a file named exactly `.env`.
- Not built: broad detection deferred; even the bounded pilot reports after the fact (notes land after the tool ran) and prevents nothing.
- Reopen evidence: a T1 credential exposure the plugin materially enabled, or an owner decision to run the bounded pilot as a separately measured experiment (debate 5 C4).
- Options: Build the bounded pilot · Defer · `Reject:` remove the item; credential exposure stays in the owner's incident process (K7).

## 8. Status shows a pending verification
- Would: `/haejwo:status` prints `verification_pending` (and `verify_fingerprint` presence) on the reviewer line, so a failed or interrupted verify reads differently from a deliberate Skip (internal docs audit #14, 2026-10-28).
- Not built: a behaviour change to `status_collect.py`; 2.35.1 is a wording-only patch. Today the setup re-run's keep-check already reports "reviewer verification pending or failed".
- Reopen evidence: a user or field session misreading a failed verification as an opt-out, or the next minor release touching `status_collect.py`.
- Options: Build (one field on the reviewer line + one clause in `commands/status.md`) · Defer · `Reject:` the keep-check reason stays the only surface.

## 9. Setup helper drops `--mode consult`
- Would: `setup_probe.py` calls the runner without `--mode consult`, matching the documented 2.34.0 usage (internal docs audit #18).
- Not built: a code change; the flag is accepted on the runner's compatibility path, so behaviour is identical.
- Reopen evidence: the compatibility path is removed, or the next release that edits `setup_probe.py`.
- Options: Build (drop two arguments, re-run the setup canaries) · Defer · `Reject:` keep the flag; the compatibility path stays.

## 10. Status uses the setup preflight
- Would: `commands/status.md` item 3 calls `setup_probe.py preflight` instead of a hand-rolled CLI probe (one source for login/version/sandbox; internal docs audit #13).
- Not built: changes what the host runs during `/haejwo:status` (a behaviour change, and preflight adds the 5 s sandbox probe).
- Reopen evidence: a field report where the hand-rolled probe and keep-check disagree.
- Options: Build · Defer · `Reject:` keep the hand-rolled probe.

## 11. PHILOSOPHY wording (K8-deferred)
- Would: (a) narrow the P7 origin clause — the PreToolUse/SessionStart paths were proven on both hosts, the PostToolUse notes and the Stop check only on Claude Code (internal docs audit #1); (b) add Docs-map rows for `AGENTS.md` (maintainer hard rules; per incident) and `docs/field/` (field evidence; per field session) (#17).
- Not built: debate 6 K8 keeps `PHILOSOPHY.md` unchanged; the plugin README Contract already states the measured host/path matrix.
- Reopen evidence: the next constitutional amendment (amendment rule), which should carry both edits.
- Options: Build at the next amendment · Defer · `Reject:` keep the current wording; the README Contract remains the measured source.

## 12. Runner note: the unresolved-variable limit stated where the note is described
- Would: the plugin README's Notes paragraph says, as the bash-guard paragraph already does for write targets, that a runner path held in a shell variable (`S=…/codex_consult.sh; $S -o …`) is not detected (no note). Field session 1 (project M, 2026-10-30): 13 of 14 runner invocations used that spelling (the 14th was `--help`, excluded by design) → 0 notes. Same patch: one README clarification that the plan-marker record scans the Agent prompt itself, so a `Plan:` line only in a referenced brief file is invisible (field session 1: 6 of 7 records `none` while the brief files carried the marker; debate 8 C3).
- Not built: documentation only; the detection limit is the 2.32 withdrawn-resolver decision and is unchanged.
- Reopen evidence: none needed — take it with the next README wording patch (word cap 2,620).
- Options: Build (one clause) · Defer · `Reject:` the bash-guard sentence covers the shared grammar.

## 13. Norm-only table: credential-use disclosure row
- Would: add "Credential use stated before use (`.env` sourcing/printing)" to the README's norm-only table with the measured rate — field session 1: 0/13 disclosures ("13 sourcing calls without prior disclosure observed", not necessarily 13 independent decisions; the host sourced production `.env` files in Bash, printed key names once with values stripped, never values); project E round 4 saw the same class. The rules already require the statement; no mechanism claims it.
- Not built: a README change at acceptance (after field session 2), not a mechanism — K3 forbids new enforcement in this attempt. Debate 8 (2026-10-30, Codex) rejected both a post-use NOTE (a note after sourcing cannot satisfy a before-use obligation, and syntax detection cannot honestly say "sourced" — a conditional segment may never run) and a deny (tightening; K3). P13's condition is field evidence AND independent cross-vendor review — one session can supply the evidence; a second incident is not required (the earlier "needs more than one session" wording was the host's error).
- Owner-process candidate (K7, outside haejwo): a Claude Code permission deny rule in the project for Bash commands that source or read `.env` — a real control, but UNVERIFIED on this host until measured against compound commands and the supported access paths (debate 8 C4).
- Reopen evidence: a measured, declared coverage failure (T1), or the owner's explicit amendment.
- Session 2 (project E, 2026-11-02; [consensus](2026-11-03-consensus-field-2.md) H1): there were 0 sourcing opportunities. The owner asked for the app to be run, the app read its own configured env, and the host listed key names once with the values masked. The omission that did occur was different: 1 episode of substantial test writes into a shared environment, not announced beforehand. Whether those writes were authorized is unresolved and stays in the owner's process (K7).
- 2.35.3: the README table carries both sessions' rows in these units.
- Options: Build (table row at acceptance) · Defer · `Reject:` keep the table at the round-4 rows.

## 14. Bash guard: heredoc bodies in the write scan
- Would: the in-place/redirect write scan skips quoted-delimiter heredoc bodies (as the push and runner matchers already do), so a `;` or a code-looking word inside a Python/stdin heredoc no longer desyncs the segment split and names prose as a target. Field session 1 (project M, 2026-10-30 13:39 KST): one wrong deny on a Markdown memory edit, recovered with Edit in 5.4 s; the maintainer's read-only analysis command was blocked the same way (`cmd.find("sed -i")` → `fanout`).
- Not built: long-standing behaviour (identical 2.31.0 → 2.35.1), declared heuristic, below the 2.20 reopen bar ("more than one retry"); the one-line candidate (`_skip_quoted_heredocs` before the scan) flips pinned adversarial cases deny→allow — 7 of 10 in the same-vendor reproduction, 6 of 10 in Codex's (SF9 stays denied); either count violates the 2.26 signed stop condition — so there is no safe fix without a shell parser. Evidence: session 4c990b2c scratchpad impl/F/repro_F.md.
- Reopen evidence: a second field session with a wrong deny that costs more than one retry, or a shell-parser-based scan reviewed cross-vendor (P13).
- Options: Build (parser-based scan + the SF rows kept) · Defer · `Reject:` the heuristic stays as declared; hosts use Edit/Write for non-code files too.

## 15. Runner note: literal-assignment resolver for the NOTE matcher only
- Would: `review_runner_invoked` resolves `NAME=/path/codex_consult.sh; … $NAME -o …` (a literal assignment earlier in the same command) for the runner NOTE only, never for any deny path. Field session 1: 13 of 14 runner invocations used that spelling → 0 notes, so the obligation the note re-presents (per-finding fact vs remedy; carry the closing statement, scope, verification, omissions into the report) was never shown; reviewer omissions reached the host's reports in 0/2 messages — co-occurrence, not causality.
- Argument for: the 2.32 resolver was withdrawn because cross-vendor review reproduced wrong DENIES (`declare -n`, …); a wrong NOTE costs ≤ 400 chars of context and no blocked action, so that withdrawal reason does not transfer.
- Against (debate 8, Codex): K1 (each surprise → a new delivery assumption), K3 (no new mechanism in this attempt), K4/K5 (reopen only on T1/T2 or the owner's explicit amendment; "after acceptance" is not an automatic 2.36); 400 chars bounds neither cumulative attention nor maintenance cost; the benefit for omission carry-over is SPECULATION.
- Not built. Reopen evidence: the owner's explicit amendment, with a canary on the installed host and cross-vendor review of the resolver's grammar (P13).
- Options: Build (resolver + canary + review) · Defer · `Reject:` the documented limit stands and #12's wording is the whole fix.

## 16. Behavioural evaluation with `claude plugin eval`
- Would: measure haejwo's effect on norm-only obligations by comparing runs with and without the plugin. The obligations: credential and side-effect disclosure, decisions stated before delegation, and delegation of suites and probes.
- Design conditions ([debate 10](2026-11-03-consensus-external-skills.md) P4):
  - blinded judging, with a different-model judge (cross-vendor where available)
  - a predeclared rubric and an opportunity denominator per obligation
  - attributed host, plugin, executor and judge revisions
  - a fixed run and cost budget
  - incomplete traces kept and reported separately
  - `--judge-model` alone establishes no independence
- Feasibility, measured 2026-11-03 (owner-authorized; Claude Code 2.1.317; a copy of the installed 2.35.2 cache — every tracked plugin file equal to tag v2.35.2, tree `haejwo/` = d86b537, compared by the Codex diff review; evidence kept in maintainer session fa7810bd scratchpad `exp16/`; 1 run per arm; $0.10; not published):
  - SessionStart injection and the edit-budget deny work inside eval children. The no-plugin arm had neither.
  - Children run with an isolated HOME: user config is not read and the reviewer is disabled.
  - On this pod any shell grant is refused (no `bwrap` or `socat`). So the bash guard, the Codex runner and every Bash-mediated norm cannot be measured here.
- Not built: the obligations of interest are Bash-mediated, so measuring them needs a system install (an owner and system decision) and then a bounded, owner-amended experiment. It has no automatic schedule and never reopens a release by itself.
- Options: Build (install the sandbox prerequisites, then one bounded experiment) · Defer · `Reject:` field sessions stay the only behavioural evidence.

## 17. Stale long-running sessions after a plugin update
- Observed: a session started on 2.33.0 kept its plugin root after the cache moved to 2.35.x and the old directory was removed. From 2026-10-30 every haejwo hook in that session failed with "Plugin directory does not exist". Hooks fail open (P4), so the session ran ungated with no notice to the model. A new session loaded 2.35.2 cleanly.
- Coverage disposition: this is outside the declared support boundary, not T2. The plugin and root READMEs already say "restart after install/update". The silent part is the host's hook-failure handling, not a haejwo path.
- Not built: haejwo cannot run code in a session whose plugin directory is gone.
- Reopen evidence: a declared path that breaks without a plugin update, or a host mechanism that could surface the failure.
- Options: Build (a louder restart notice in the install docs or the update release notes) · Defer · `Reject:` the existing "restart after install/update" line stands.

## 18. Setup guidance when the account rejects the reviewer model
- Observed (2026-11-02): an external user on a lower-tier ChatGPT plan reported that the reviewer "does not work because model 5.3 is missing".
  - haejwo hard-codes no model; the runner resolves `CODEX_MODEL` > `codex.model` > the CLI default.
  - The runner already prints a "model … was rejected before execution" hint.
  - The setup flow does not tell the user to pick a model their plan allows (setup → Custom → reviewer model, or `model=` in the Codex config).
- Not built: documentation only, and a single report.
- Reopen evidence: a second report, or the owner's word. It can then ride the next wording patch.
- Options: Build (one sentence in `commands/setup.md` and `docs/runners.md`) · Defer · `Reject:` the runner hint is enough.

## Closed
- 19. Reviewer contract: provenance labels. ADOPTED in 2.35.3 (owner amendment 2026-11-03; [debate 10](2026-11-03-consensus-external-skills.md) P1 → the plan critique in the ledger).
  - Both runners' REVIEWER CONTRACT now asks: "For each finding, label provenance as regression (introduced by this change), base (already present), or unresolved (baseline unavailable, inapplicable, or insufficient), and flag any requested remedy beyond the brief or plan."
  - Source idea: addyosmani/agent-skills (MIT), paraphrased and narrowed.
  - Evidence: the session-2 classification in #5.
  - The "zero findings is valid / do not invent" half was dropped, because 0 invented findings were observed.
  - The labels are the reviewer's claims. They block, waive and route nothing. No benefit (for example, fewer rounds) is claimed.
- D11 (preserve the edit budget across hand-back turns): CLOSED as not adopted (debate 6) — a tightening without demonstrated need. Reopen only under T1/T2.

## Rejected alternatives
- 2.35.0 — keep the current setup flow + documentation only (say setup is once per machine): rejected because the measured cost is in the flow itself — on project E ≈ 4–5 min, ≈ 10 model turns, three question rounds and a read-only attempt plus retry that a 36 ms preflight already shows cannot succeed here — and documentation removes none of it (owner amendment, docs/field/STABILIZATION-2034.md).
