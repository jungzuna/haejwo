#!/usr/bin/env python3
"""haejwo turn-reset — UserPromptSubmit.

A user prompt = a new turn: reset this session's distinct-file counter.
(gate.py also lazy-resets on prompt_id change, so either mechanism alone
is sufficient — this is belt and braces.)

Turn checklist (2.33.0 PILOT, `gate.turn_checklist`, default true): AFTER
the reset (unchanged; no exemption below ever skips it), one CHECKLIST line
as UserPromptSubmit additionalContext carrying this session's counts from
the session counters the reset carried (hand-backs, review-runner
invocations, push-bearing calls — incremented by handback_nudge.py,
post_bash.py and bash_guard.py, never here). Emitted on a Claude host for
the main agent only, with config.json not malformed, `gate.enabled` and
`gate.turn_checklist` true and HAEJWO_GATE not off; a lock not taken skips
the reset and the line. Assignment and emission are logged separately
(diff review D5): the session's assignment ONCE per session (`hook:
turn_checklist, decision: none, via: assigned-on | assigned-off`; session
flag `checklist_assigned`, set in the reset's own save and carried by
carry_session_flags; an unsaved flag writes no record, it would repeat),
and every emission (`decision: note, via: emitted, chars`). "off" means
either flag not true (an invalid value included). Exempt paths (HAEJWO_GATE
off, a Codex host, malformed config) assign nothing and emit nothing.
Origin: field round 3, root-cause consensus 2026-10-26: fact-based
re-presentation; measured on Claude Code 2.1.310 headless (the context is
delivered the same turn and persists into later turns).
"""
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    _is_count, carry_session_flags, emit_context, gate_disabled_by_env,
    is_subagent, load_config_with_status, load_state, observe, on_codex_host,
    paths, prune_state, read_payload, save_state, state_lock,
)

CHECKLIST = (
    "[haejwo turn] So far: {h} hand-backs, {r} review runs, {p} push-bearing "
    "calls. Match approval to listed actions, scope, order. Decide each "
    "material finding fact vs remedy, with reasons. At acceptance link the "
    "final diff to the review and its closing statement; label worker-run and "
    "same-model results."
)


def render_checklist(state):
    def count(key):
        v = state.get(key)
        return v if _is_count(v) else 0
    return CHECKLIST.format(h=count("handbacks"), r=count("review_runs"),
                            p=count("push_calls"))


def checklist_mode(data, root):
    """"on" / "off" for the turn checklist, or None on an exempt path
    (HAEJWO_GATE off, a Codex host, malformed config, any error). Never
    raises: an error here must not skip the reset."""
    try:
        if gate_disabled_by_env() or on_codex_host(root, data):
            return None
        cfg, status = load_config_with_status(data)
        if status == "malformed":
            return None
        gate = cfg.get("gate") or {}
        return ("on" if gate.get("enabled") is True
                and gate.get("turn_checklist") is True else "off")
    except Exception:
        return None


def _observe(data, rec):
    try:  # never load-bearing
        observe(data, rec)
    except Exception:
        pass


def main():
    os.umask(0o077)  # cold-loop F15: state files are per-user
    payload = read_payload()
    if not payload or is_subagent(payload):
        sys.exit(0)
    root, data = paths(sys.argv)
    sid = payload.get("session_id", "unknown")
    # The turn counter resets; SESSION-scoped once-note flags must survive it
    # (hjw_common.carry_session_flags), the session counters too; TURN-scoped
    # flags (`outward_attempt`, `outward_calls`, `outward_ids`, `report_nudged`
    # — report_check.py) are not carried. Held under the same session lock the
    # other writers use, so a concurrent gate/delegation write between our
    # read and save cannot be lost. A lock not taken within its bound means
    # no session-bookkeeping write at all — not even the prune (never
    # unlocked); gate.py's lazy prompt_id/turn_id reset still starts the
    # new turn.
    mode = checklist_mode(data, root)
    with state_lock(data, sid) as lk:
        if not lk.acquired:
            sys.exit(0)
        state = carry_session_flags(
            load_state(data, sid),
            {"prompt_id": payload.get("prompt_id"), "files": []})
        assign = mode is not None and not state.get("checklist_assigned")
        if assign:
            state["checklist_assigned"] = True
        saved = save_state(data, sid, state) is None
    prune_state(data)
    if assign and saved:
        _observe(data, {"v": 2, "hook": "turn_checklist", "decision": "none",
                        "via": "assigned-" + mode, "sid": str(sid)[:12]})
    if mode == "on":
        text = render_checklist(state)
        _observe(data, {"v": 2, "hook": "turn_checklist", "decision": "note",
                        "via": "emitted", "chars": len(text),
                        "sid": str(sid)[:12]})
        emit_context("UserPromptSubmit", text)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
