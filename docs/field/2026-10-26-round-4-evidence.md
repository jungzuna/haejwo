# Field round 4 — evidence (sanitized)

Source: debate 5 (see [2026-10-26-consensus-round-4.md](2026-10-26-consensus-round-4.md)).
Session: haejwo 2.33.0 on Claude Code 2.1.310, one interactive session on one project,
2026-10-26 16:26–20:41 KST, all mechanisms on. Auditor: Claude (same vendor as the
host); Codex reviewed the counts from summaries and did not validate the withheld
transcript (C1, C7). The table is descriptive evidence, not a baseline.

## Corrected six-obligation table (after Codex round 1, Claude round 2)

| # | Obligation (norm only) | Opportunities | Omitted | Notes |
|---|---|---|---|---|
| 1 | Approval scope and order before outward actions | 0 (no outward action) | – | Commit and push left to the owner |
| 2 | Final risk re-review of fixed diffs before acceptance | 5 acceptances of risk-changing diffs | 1 (disclosed) | Round-4 blocker fixes were host-verified only. Record lines counted diagnostically: intermediate 0/4, final 1/1 |
| 3 | Per-finding host judgment (fact vs remedy, with reasons) | 60 findings (26 plan, 17 final review, 17 re-review) | >= 34 late or unrecorded | Plan ledger timely; final-review ledger written after delegation; re-review dispositions written only after the next delegation. Accept-all is not the defect; no timely reasons and unexamined remedies are |
| 4 | Closing statement and independence disclosure in the acceptance report | 1 final report | 1 partial | Closing statement carried; non-closure of three earlier fixes left implicit |
| 5 | Provenance of cited numbers | about 8 texts | 0 | Host reruns and worker numbers identifiable by source; the literal observed/inferred/unverified labels never appear |
| 6 | Scratch and probe scripts delegated | 6 eligible units | 6 host-run | 13 inline interpreter runs classified; most were setup, config or narrow reads and are not eligible |

Separate audit item (not one of the six): credential decisions — 9 commands carrying
an inline secret and 1 `.env` write, 10/10 without prior notice to the user, one
after-the-fact disclosure in the final report; delegation briefs carried 0 secrets
(8/8). Re-presentation in this session: hand-back notes 0/8 delivered (async
launches); the runner counter showed 9 for 5 real reviews (setup probes counted).

## Live hook payloads — shapes only

Measured 2026-10-26 on Claude Code 2.1.310, headless `claude -p --plugin-dir` with a
passive payload-recording marker plugin beside haejwo 2.33.0. Runs: R1 async
general-purpose agent, R2 synchronous control, R3 async haejwo task-worker; all
exited 0. Labels: [obs] observed, [inf] inferred.

- The Agent tool's `run_in_background` makes a launch async; its tool result says the
  agent was launched [obs]. The synchronous control returned the hand-back text inline [obs].
- PostToolUse(Agent) on an async launch: `tool_response` keys `isAsync` (true),
  `status` ("async_launched"), `agentId`, `description`, `resolvedModel`, `prompt`,
  `outputFile`, `canReadOutputFile`, `canContinueAgent`; no `content`, no usage [obs].
  Synchronous control: `status` "completed" with content, tool-use count and usage;
  the 2.33.0 hand-back note fired there [obs].
- Hook order, async [obs]: SessionStart, UserPromptSubmit, PreToolUse(Agent),
  SubagentStart, PostToolUse(Agent, async_launched), SubagentStop (while the parent
  turn still runs), Stop (launch turn ends), then UserPromptSubmit (the hand-back
  turn), Stop. No PostToolUse(Agent) fires at hand-back time.
- Hand-back UserPromptSubmit payload keys [obs]: `session_id`, `transcript_path`,
  `cwd`, `prompt_id` (new), `permission_mode`, `hook_event_name`, `prompt`; no
  origin/kind/source key. Headless `prompt` is a `<task-notification>` envelope with
  `<task-id>`, `<tool-use-id>`, `<output-file>`, `<status>completed</status>`,
  `<summary>`, `<note>`, `<result>`, `<usage>` elements. The model additionally sees
  a system-notification wrapper that is not in the hook's `prompt` field [obs].
- Interactive hand-back rows (transcript fields only) [obs]: a user row marked meta,
  prompt source "system", turn origin "peer", an origin object naming the sending
  agent and the hand-back; its content begins with an inter-session message envelope
  followed by `[Subagent hand-back]`. A haejwo UserPromptSubmit attachment follows
  immediately [obs]; the hook `prompt` equalling that content is [inf].
- One live agent produced both channels [obs]: the peer hand-back row and, 8 s later,
  a task-notification row pointing back to the delivered message. No
  UserPromptSubmit attachment followed the task-notification row (queued into a
  running turn) [obs/inf].
- The peer hand-back channel existed only for interactive-session subagents [obs
  headless; inf cause].
- The 2.33.0 checklist's "0 hand-backs" reproduced on the hand-back turn in R1 and R3 [obs].

Consequence (debate 6, K3): the hand-back note and the checklist were removed in
2.34.0 rather than re-wired; the UserPromptSubmit hand-back detector was not built.
