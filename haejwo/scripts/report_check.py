#!/usr/bin/env python3
"""haejwo report-check — Stop.

Mechanism: when the main agent stops in a turn whose session state carries
`outward_attempt` (set by bash_guard.py when it let a main-agent `git push`
through this turn), and the final assistant message lacks the literal
`Outward actions:` or the literal `Final-diff review:`, this hook returns
`{"decision": "block", "reason": ...}` ONCE per turn (`report_nudged`, set
under the session lock BEFORE the block is printed), so the model continues
and writes the record lines. It checks PRESENCE only (P5): a label proves
neither review adequacy nor authorization, and `not run because: <reason>`
satisfies the review label. It never emits permissionDecision and never
approves anything (P3).

Exemptions (exit 0, no output — fail open, P4): malformed/empty payload;
`stop_hook_active` truthy; subagent payload; HAEJWO_GATE=off; a Codex host
(its Stop contract is not measured); config.json malformed; `gate.enabled`
or `gate.report_check` false (an invalid `gate.report_check` value turns the
check OFF, like every other enforcement flag — hjw_common._TYPED_KEYS);
`last_assistant_message` absent or not a string; the session lock not taken
within its bound; no `outward_attempt` in this turn's state (including a
state whose prompt_id/turn_id differs from the payload's); `report_nudged`
already set; both labels present; the once-flag could not be saved.

Facts (2.33.0, field round 3 bundle B): the reason ends with the turn's
detected push-bearing tool calls, `Detected push-bearing tool calls this
turn: N (id, ...; +k more).` — N is `outward_calls`, the ids are
`outward_ids` (at most 8, bash_guard.py); a state carrying only the 2.32.0
`outward_attempt` flag (no count) keeps the plain reason, and a count with no
recorded id says "ids not recorded". The block observation carries `chars`
(the reason's length: per-session exposure is countable, README).

Scope: push-only pilot; the consensus trigger "acceptance report" is not
countable and is NOT implemented. `outward_attempt` and `report_nudged` are
TURN scoped: every turn reset rebuilds the state and carry_session_flags
does not copy them.
"""
import json
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    gate_disabled_by_env, is_subagent, load_config_with_status, load_state,
    observe, on_codex_host, paths, read_payload, save_state, state_lock,
)

OUTWARD_LABEL = "Outward actions:"
REVIEW_LABEL = "Final-diff review:"
REASON = (
    "[haejwo] This turn requested git push. Finish with the record lines "
    "(presence is all this hook checks): `Outward actions: <repo; branch; "
    "action; result> - authorized by: <user words, time>` and `Final-diff "
    "review: <change id; reply; closing statement | not run because: "
    "<reason>>`."
)


FACTS = " Detected push-bearing tool calls this turn: {n} ({ids}{more})."


def block_reason(state):
    """REASON plus the turn's recorded push-bearing tool calls (FACTS)."""
    ids = state.get("outward_ids")
    ids = [x for x in ids if isinstance(x, str)][:8] \
        if isinstance(ids, list) else []
    n = state.get("outward_calls")
    n = n if isinstance(n, int) and not isinstance(n, bool) else 0
    n = max(n, len(ids))
    if n == 0:
        return REASON
    more = f"; +{n - len(ids)} more" if ids and n > len(ids) else ""
    return REASON + FACTS.format(n=n, ids=", ".join(ids) or "ids not recorded",
                                 more=more)


def missing_via(message):
    """The observation `via` for a message, or None when both labels are
    present."""
    no_out = OUTWARD_LABEL not in message
    no_rev = REVIEW_LABEL not in message
    if no_out and no_rev:
        return "missing-both"
    if no_out:
        return "missing-outward"
    if no_rev:
        return "missing-review"
    return None


def main():
    os.umask(0o077)  # cold-loop F15: state files are per-user
    payload = read_payload()
    if not isinstance(payload, dict) or not payload:
        sys.exit(0)
    if payload.get("stop_hook_active") or is_subagent(payload):
        sys.exit(0)
    if gate_disabled_by_env():
        sys.exit(0)
    root, data = paths(sys.argv)
    if on_codex_host(root, data):
        sys.exit(0)
    cfg, status = load_config_with_status(data)
    gate = cfg.get("gate") or {}
    if status == "malformed" or gate.get("enabled") is not True \
            or gate.get("report_check") is not True:
        sys.exit(0)
    message = payload.get("last_assistant_message")
    if not isinstance(message, str):
        sys.exit(0)
    via = missing_via(message)
    if via is None:
        sys.exit(0)
    sid = payload.get("session_id", "unknown")
    pid = payload.get("prompt_id") or payload.get("turn_id")
    with state_lock(data, sid) as lk:
        if not lk.acquired:
            sys.exit(0)  # never block without having recorded the nudge
        state = load_state(data, sid)
        if pid and state.get("prompt_id") and state.get("prompt_id") != pid:
            sys.exit(0)  # the flags belong to an earlier turn
        if state.get("outward_attempt") is not True or state.get("report_nudged"):
            sys.exit(0)
        state["report_nudged"] = True
        if save_state(data, sid, state) is not None:
            sys.exit(0)  # the once-flag did not persist: no block
        reason = block_reason(state)
    try:  # never load-bearing: an observation failure keeps the block
        observe(data, {"v": 2, "hook": "report_check", "decision": "block",
                       "via": via, "chars": len(reason), "sid": str(sid)[:12]})
    except Exception:
        pass
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
