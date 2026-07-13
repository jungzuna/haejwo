# haejwo PHILOSOPHY — the constitution

haejwo was built through real incidents; these are the rules that survived
contact. Read this BEFORE changing anything — code, prompts, or docs. It is
constitutional, not archival: each principle carries the reason it exists.

## Identity
haejwo (해줘 — English: "just handle it") is the **cold-start plugin** for
Claude Code and Codex — they are the official harnesses; haejwo is a
lubricant layer that makes multiple models run well on top. The user just
talks; the host judges, tiers execute, gates enforce. 해줘 names the EXPERIENCE, not the mechanism (the mechanism is
closer to 시켜, "make others do it"); that is deliberate benefit-naming, and
it stays honest only while the name-integrity rule holds: **the moment users
must understand or manage the plugin to get their work done, 해줘 stops
being true.**

## Principles
1. **Preserve the name promise.** The user never manages workflow; orchestration
   and plugin failures are host-owned. *[first-run and recovery design]*
2. **Spend model budget where judgment compounds.** Planning, analysis, and
   review are the product; implementation is the cheap tail. Reviewer effort
   scales with the decision's stakes — uniform max dilutes it; cheaper tiers
   implement and do chores; trivial 1-2 file work stays with the host.
   *[planning outweighs implementation; uniform maximums spend effort exactly
   where it compounds least]*
3. **Enforce economics physically.** Prompts can guide; gates and budgets must
   make expensive mistakes hard. *[instructions alone don't bind; the
   PreToolUse gate does]*
4. **Never brick the session.** Gates are plugin affordances, not security
   boundaries; on any error or ambiguity, fail open. *[failure-classifier design]*
5. **Hard-gate only countable invariants.** Semantic judgments get norms and
   nudges; outward or irreversible actions get ask-once consent registries.
   *[strict rules on judgment calls break the just-talk concept]*
6. **A denial must steer.** Every deny tells the model exactly what to do
   instead. *[deny→delegate payload]*
7. **Verify side effects in the real environment.** rc=0, stale docs, and
   confident claims are not proof. *[sandboxes can fail silently at rc=0;
   every hook behavior here was proven live before shipping]*
8. **Field observation beats speculation.** Watch real sessions, fix what
   actually broke, and delete surfaces users don't recognize.
   *[the best fixes came from watching, not speculating]*
9. **Independent review requires a different model.** Same-model subagents are
   for parallelism and isolation, not authority. *[same-model review launders
   authority]*
10. **Disagreement and discretion are signal.** The reviewer must rebut; the
    host decides with a documented reason; an informed owner overrules theory;
    true deadlock goes to the user; workers disclose judgment calls instead of
    resolving forks silently. Never fake-converge, never decide invisibly.
    *[identical briefs can produce divergent implementations while the worker
    never flags the fork — disclosure must be explicit]*
11. **Reporting stays proportional.** Format follows content; one honest
    checkpoint beats progress theater. *[reports exist for the reader, not
    the reporter]*
12. **The tool obeys its own rules.** Maintenance runs under the same gates it
    imposes. *[the gate forced its own maintainer to delegate a test]*
13. **Constraints are loans.** Model-dependent constraints — physical gates,
    behavioral norms, session-injected instructions — must keep earning their
    cost. When a role's configured model family or tier changes, re-audit the
    constraints calibrated to that role against representative field evidence
    and their origin failure. Keep a physical gate while it protects its
    countable invariant better than a thinner mechanism. Tightening requires
    field evidence and independent adversarial rebuttal (cross-vendor when
    available; P3-gate removals always get independent review); relaxing
    requires an evidence-bearing re-audit and a rollback signal — never a new
    failure incident. Durable owner-policy and safety constraints do not decay
    with model quality. *[origin: C4 deferral — marker drift 0/3→8/8 closed
    without a gate; uniform xhigh overuse; pre-Fable calibrations with no
    re-audit path]*

## Precedence (when principles collide, the higher tier wins)
1. **Session & trust safety** — fail open, never brick, consent before
   outward/irreversible actions
2. **Honest evidence** — facts get measured before anyone invokes authority
3. **Owner's explicit, informed decision** on value tradeoffs
4. **Name-integrity / user experience**
5. **Concise & clear**
6. **Lean** — delete over add

Worked example (the hardest conflict): push consent vs 해줘's
"don't ask me things". Consent won — ask once — and the per-repo registry then
restored the name promise. *[the push-consent registry]*

Meta-note: the owner sits ABOVE this document, not inside tier 3. Tier 3 covers
in-flight value calls; changing the standing order itself is an amendment
(below). "Informed" means the host presents contrary evidence (tier 2) BEFORE
the owner decides.

## Docs map (change cadences differ — do not merge the layers)
| Layer | File | Changes |
|---|---|---|
| Constitution | `PHILOSOPHY.md` (this) | almost never, by amendment |
| Prompt style law | `PROMPTS.md` | occasionally |
| Product & usage | `README.md` | per feature |
| Runtime operating rules | `rules/orchestration.md` (session-injected, budgeted) | per feature |

## Amendment rule
Amend only when a real incident proves a durable rule or conflict. Every
amendment names its origin case and removes or merges any principle it
duplicates. Wording changes bump the plugin patch version.
