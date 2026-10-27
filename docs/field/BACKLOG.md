# Backlog — mechanisms not built

Source: debate 6 K3 ("Not built → backlog with an explicit reject option") and K4
([2026-10-27-consensus-finish.md](2026-10-27-consensus-finish.md)). These items never
reopen a release on their own: only T1 (a material safety or authorization incident
the plugin caused or materially enabled, or a failure of its declared coverage; a
reproducible material regression against the frozen contract) or T2 (a demonstrated
failure of a declared supported host/path assumption) does. The backlog is reviewed
at most quarterly. Declining an item is a recorded outcome (K6): choosing `Reject:`
removes it from this file with the date and the reason.

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

## Closed
- D11 (preserve the edit budget across hand-back turns): CLOSED as not adopted (debate 6) — a tightening without demonstrated need. Reopen only under T1/T2.

## Rejected alternatives
- 2.35.0 — keep the current setup flow + documentation only (say setup is once per machine): rejected because the measured cost is in the flow itself — on project E ≈ 4–5 min, ≈ 10 model turns, three question rounds and a read-only attempt plus retry that a 36 ms preflight already shows cannot succeed here — and documentation removes none of it (owner amendment, docs/field/STABILIZATION-2034.md).
