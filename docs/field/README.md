# Field record

This folder holds the evidence behind haejwo's published contract (plugin README,
`## Contract (2.34)`): the signed consensus documents of the field debates, the
sanitized measurements they rest on, the backlog of mechanisms that were NOT built,
and the stabilization ledger of the one capped 2.34.0 attempt. Debates and
measurements used to live only in session scratchpads; debate 6 (K8) moved them
here so a reader can check a claim without the session that produced it. Nothing
here contains credentials, transcript excerpts or machine-local paths; raw
transcripts stay withheld (they contain owner credentials).

| File | What it is |
|---|---|
| [2026-10-26-consensus-round-4.md](2026-10-26-consensus-round-4.md) | Debate 5, field round 4 (haejwo 2.33.0 live session): Claude x Codex consensus C1-C9, D11 open (verbatim) |
| [2026-10-26-round-4-evidence.md](2026-10-26-round-4-evidence.md) | The corrected six-obligation omission table and the live hook-payload shapes (sanitized) |
| [2026-10-27-consensus-finish.md](2026-10-27-consensus-finish.md) | Debate 6, "Can haejwo be finished?": K1-K9, the contract, the 2.34.0 scope, the reopen triggers T1/T2 (verbatim) |
| [BACKLOG.md](BACKLOG.md) | Mechanisms not built, each with an explicit reject option and the evidence that would reopen it |
| [STABILIZATION-2034.md](STABILIZATION-2034.md) | K5 ledger of the 2.34.0 attempt: budget, retained claims and their exposure, canaries, field sessions, closure |

Rules for this folder: consensus files are copied verbatim and never edited (a later
debate supersedes, it does not rewrite); evidence files carry shapes, counts and
denominators, never payload dumps; every backlog item keeps its `Reject:` line.
