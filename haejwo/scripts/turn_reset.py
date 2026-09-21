#!/usr/bin/env python3
"""haejwo turn-reset — UserPromptSubmit.

A user prompt = a new turn: reset this session's distinct-file counter.
(gate.py also lazy-resets on prompt_id change, so either mechanism alone
is sufficient — this is belt and braces.)
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    is_subagent, load_state, paths, prune_state, read_payload, save_state,
)


def main():
    payload = read_payload()
    if not payload or is_subagent(payload):
        sys.exit(0)
    root, data = paths(sys.argv)
    sid = payload.get("session_id", "unknown")
    state = {"prompt_id": payload.get("prompt_id"), "files": []}
    # The turn counter resets; SESSION-scoped flags must survive it. The
    # malformed-config note is emitted once per session, shared by gate.py /
    # bash_guard.py / delegation_gate.py (hjw_common.malformed_note_once), so
    # losing this flag here would repeat it every turn.
    if load_state(data, sid).get("cfg_malformed_noted"):
        state["cfg_malformed_noted"] = True
    save_state(data, sid, state)
    prune_state(data)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
