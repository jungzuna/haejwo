#!/usr/bin/env python3
"""haejwo turn-reset — UserPromptSubmit.

A user prompt = a new turn: reset this session's distinct-file counter.
(gate.py also lazy-resets on prompt_id change, so either mechanism alone
is sufficient — this is belt and braces.)
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    is_subagent, paths, prune_state, read_payload, save_state,
)


def main():
    payload = read_payload()
    if not payload or is_subagent(payload):
        sys.exit(0)
    root, data = paths(sys.argv)
    sid = payload.get("session_id", "unknown")
    save_state(data, sid, {"prompt_id": payload.get("prompt_id"), "files": []})
    prune_state(data)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
