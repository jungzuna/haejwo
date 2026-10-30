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
- Options: Build · Defer · `Reject:` remove the item.

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
- Would: the plugin README's Notes paragraph says, as the bash-guard paragraph already does for write targets, that a runner path held in a shell variable (`S=…/codex_consult.sh; $S -o …`) is not detected (no note). Field session 1 (project M, 2026-10-30): 11 of 12 runner invocations used that spelling → 0 notes.
- Not built: documentation only; the detection limit is the 2.32 withdrawn-resolver decision and is unchanged.
- Reopen evidence: none needed — take it with the next README wording patch (word cap 2,620).
- Options: Build (one clause) · Defer · `Reject:` the bash-guard sentence covers the shared grammar.

## 13. Norm-only table: credential-use disclosure row
- Would: add "Credential use stated before use (`.env` sourcing/printing)" to the README's norm-only table with the measured rate — field session 1: 0/8 disclosures (host sourced the API-DP `.env` eight times in Bash, printed key names only, never values); project E round 4 saw the same class. The rules already require the statement; no mechanism claims it.
- Not built: a README change at acceptance (after field session 2), not a mechanism — K3 forbids new enforcement in this attempt, and a `.env`-sourcing deny would be a tightening without the P13 evidence.
- Reopen evidence: a second session with the same omission rate makes it the table's leading row.
- Options: Build (table row at acceptance) · Defer · `Reject:` keep the table at the round-4 rows.

## 14. Bash guard: heredoc bodies in the write scan
- Would: the in-place/redirect write scan skips quoted-delimiter heredoc bodies (as the push and runner matchers already do), so a `;` or a code-looking word inside a Python/stdin heredoc no longer desyncs the segment split and names prose as a target. Field session 1 (project M, 2026-10-30 13:39 KST): one wrong deny on a Markdown memory edit, recovered with Edit in 5.4 s; the maintainer's read-only analysis command was blocked the same way (`cmd.find("sed -i")` → `fanout`).
- Not built: long-standing behaviour (identical 2.31.0 → 2.35.1), declared heuristic, below the 2.20 reopen bar ("more than one retry"); the one-line candidate (`_skip_quoted_heredocs` before the scan) flips 7 of 10 pinned adversarial cases SF2–SF10 deny→allow — the 2.26 signed stop condition — so there is no safe fix without a shell parser. Evidence: session 4c990b2c scratchpad impl/F/repro_F.md.
- Reopen evidence: a second field session with a wrong deny that costs more than one retry, or a shell-parser-based scan reviewed cross-vendor (P13).
- Options: Build (parser-based scan + the SF rows kept) · Defer · `Reject:` the heuristic stays as declared; hosts use Edit/Write for non-code files too.

## Closed
- D11 (preserve the edit budget across hand-back turns): CLOSED as not adopted (debate 6) — a tightening without demonstrated need. Reopen only under T1/T2.

## Rejected alternatives
- 2.35.0 — keep the current setup flow + documentation only (say setup is once per machine): rejected because the measured cost is in the flow itself — on project E ≈ 4–5 min, ≈ 10 model turns, three question rounds and a read-only attempt plus retry that a 36 ms preflight already shows cannot succeed here — and documentation removes none of it (owner amendment, docs/field/STABILIZATION-2034.md).
