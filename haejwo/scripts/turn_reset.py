#!/usr/bin/env python3
"""haejwo turn-reset — UserPromptSubmit.

A user prompt = a new turn: reset this session's distinct-file counter.
(gate.py also lazy-resets on prompt_id change, so either mechanism alone
is sufficient — this is belt and braces.)
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    carry_session_flags, is_subagent, load_state, paths, prune_state,
    read_payload, save_state, state_lock,
)


def main():
    payload = read_payload()
    if not payload or is_subagent(payload):
        sys.exit(0)
    root, data = paths(sys.argv)
    sid = payload.get("session_id", "unknown")
    # The turn counter resets; SESSION-scoped once-note flags must survive it
    # (hjw_common.carry_session_flags). Held under the same session lock the
    # other writers use, so a concurrent gate/delegation write between our
    # read and save cannot be lost.
    with state_lock(data, sid):
        state = carry_session_flags(
            load_state(data, sid),
            {"prompt_id": payload.get("prompt_id"), "files": []})
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
