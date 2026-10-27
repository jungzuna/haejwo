# Debate 6 consensus — "Can haejwo be finished?" (signed 2026-10-27)

Parties: Claude (Fable 5.1, host session 4c990b2c) × Codex (cli-default, effort high,
consult mode, two rounds: codex_r1.md 9.4 KB, codex_r2.md). Codex SIGNED K2, K5, K8, K9
and NARROWED K1, K3, K4, K6, K7; no OBJECT. Claude accepted every narrowing verbatim.
Claude withdrew: "N is unavoidable in rate", "fail-open limits drift to missing nudges",
the 60-line keep/remove threshold.

K1 Diagnosis (NARROWED). Residual model and host failures remain possible; the
expansion loop (observed omission → new obligation/detector/counter → new delivery
assumption → next field surprise) plausibly contributes to recurrence. Release count
measures activity, not convergence against a stable specification. In the supplied
four-round reports the heredoc gap is the only identified failure of the narrowly
defined enforcement core (reported evidence, not an independent audit or a
reliability estimate); the earlier 2.27.0 auto-approve failure lies outside that
interval (cold loop). Other defects were norm omissions, host-boundary drift, or
pilot code shipped in the same release.

K2 "Finished" (SIGNED) = a bounded, supported version, not the absence of future bad
behaviour. One CONTRACT section in README (not PHILOSOPHY) states: (i) enforcement
mechanisms with exemptions; (ii) non-blocking re-presentation kept only where delivery
was demonstrated on the installed host — demonstrated delivery stated separately from
demonstrated behavioural benefit; (iii) norm-only obligations with the last measured
omission rate and denominator; (iv) the supported host/path matrix with the version
each path was measured on; (v) everything else labelled unverified.

K3 Stabilization release 2.34.0, one attempt, minor (NARROWED). Remove checklist
emission, its pilot bookkeeping/config definition, and the hand-back PostToolUse entry
(matcher `^(Task|Agent)$`); preserve `turn_reset.py`, its UserPromptSubmit
registration, the budget reset and the retained turn-state lifecycle. Obsolete config
keys in user files load harmlessly; user configuration is never rewritten. Remove
misleading review counts; any retained note says only "detected runner call" with its
detection limit (syntax detected; execution, completion and review quality NOT
established), setup probes excluded, an exit status only when the tool result supplies
one (identified as the containing Bash call's status). The debate-5 C2(a)
UserPromptSubmit hand-back detector is NOT built (its only consumer was the checklist).
Keep (K3d, conditional per R2): bash_guard push note → outward-attempt → Stop
block-once (push-only) with its FACTS line, frozen as exactly the presence-check claim
(two literal labels, blocks once, explicit exemptions; verifies neither authorization,
push success nor review adequacy); supported only on the measured path (Claude Code
2.1.310), canary within the K5 budget, field exposure "unverified" (0 push turns in
round 4), never generate a real push merely for exposure. Keep: gate budget,
Bash-write deny, delegation model pin, plan-marker presence record, session brief.
Ship with it (wording): deny-text eligibility sentence, `via` label split
(observability only), runner `--mode` examples and error text, README (nested
delegation, resolution-based temp exemption, notes land post-run, keep review
artifacts). Not built → backlog with an explicit reject option: missing-marker note,
acceptance-record reminder, completion ledger, round detector, interpreter reminders,
credential detector. D11 CLOSED as not adopted (a tightening without demonstrated
need). rules/orchestration.md net bytes ≤ 0, ratchet 3,750. Tests updated for removed
behaviour; reset and retained-hook coverage preserved; commit gated on the unpiped
`python3 tests/test_hooks.py` exit.

K4 Reopen triggers (NARROWED), only these:
 T1 — a confirmed material safety or authorization incident caused or materially
 enabled by the plugin, or demonstrating failure of its declared coverage; or a
 reproducible material regression against the frozen contract, including misleading
 evidence (wrong counters/records), permission interference, or loss of required
 review artifacts.
 T2 — demonstrated failure of a declared supported host/path assumption. A new
 upstream version alone is not demonstrated drift.
Everything else → docs/field/BACKLOG.md with a mandatory delete/reject option per
item, reviewed at most quarterly; nonmaterial omissions and repeated cosmetic findings
never reopen a release.

K5 Stop rule (SIGNED). One stabilization attempt capped at five maintainer working
days (a policy cap, not an evidence-derived optimum; start time and cumulative
maintainer time recorded) and two field sessions on different projects, no automatic
extension. Before starting: enumerate retained claims and the exposure each needs.
Run the regression suite and installed-host canaries for every supported path; field
sessions exercise the remaining field-dependent claims. Acceptance requires zero
unresolved material contract failures; missing exposure is "unverified", never
"passed". At the ceiling: remove the unsupported claims/features or withhold the
release. After acceptance, scheduled monitoring and debates END; reopen only under
T1/T2. Two sessions are an operational checkpoint, not statistical validation.

K6 Evidence integrity (NARROWED). Counters name the counted event and scope; rates
include their denominator; records identify their source, scope and detection
limits. Remove evidence displays that cannot meet these requirements. `/haejwo:status`
is supplementary; user reports and targeted compatibility checks are the maintenance
signals. Every proposed mechanism carries an explicit deletion/rejection option so
that declining an addition is a recorded outcome.

K7 Plugin defect vs workflow incident (NARROWED). Unauthorized outward actions and
credential exposure by the host model are recorded and handled in the owner's
incident process, never dismissed as model noise. Reopen plugin work under T1 when
the plugin caused or materially enabled the incident, or its declared coverage
failed; otherwise it stays in the owner's process.

K8 (SIGNED) PHILOSOPHY unchanged (P7 already requires real-environment verification;
"measure every path" is unbounded and duplicates it). Per-release process unchanged
(C8). Debate and measurement artefacts move from scratchpads into docs/field/.

K9 Owner answer (SIGNED, Codex wording): "You can finish a bounded version properly:
verify its declared mechanisms, remove unsupported extras, publish its limits, and
stop expanding its obligations. You cannot guarantee flawless future behavior across
changing models and hosts, but you can end this development loop with a fixed
acceptance budget and explicit maintenance triggers."

Residual (recorded, Codex): signature conditional on the narrowings above; Codex does
not independently endorse the historical measurements, omission rates, C2 wording
changes or the full retained-mechanism inventory (read from summaries; PHILOSOPHY text
excluded from round 2, so P13 compliance of the removals is not certified by Codex —
removals are loosenings, not tightenings, in Claude's reading). Audit limits: same
material limits as debate 5 (owner credentials in the raw transcripts → withheld).

Supersedes: debate-5 C2(a)(b)(d)(e) and C3's hand-back canaries (no longer needed);
C2(c)(f)(g)(h), C4, C5, C7, C8, C9 stand. D11 closed.
