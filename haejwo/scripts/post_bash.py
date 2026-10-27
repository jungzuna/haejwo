#!/usr/bin/env python3
"""haejwo post-bash — PostToolUse on Bash.

Mechanism: after a main-agent Bash call returns, this hook re-presents an
existing obligation next to the facts of that call, as additionalContext
(a note: never a permission decision, never a block):
  push-returned   the call's tool_use_id is in the session's `outward_seen`
                  (bash_guard.py recorded it as push-bearing; `outward_ids`
                  when a state has no `outward_seen`; the state's prompt_id
                  must equal the payload's) and is claimed, under the
                  session lock, in the session list `push_returned_ids`
                  (cap 64; a claimed id gets no second line). Both lists
                  stop at 64 ids: later push-bearing calls get no line. The
                  line quotes the last line of stderr, else of stdout, that
                  is non-empty AFTER sanitizing (hjw_common.sanitize_quoted:
                  an ANSI-only last line is skipped), else "(no output)",
                  labelled quoted shell output: it states that the call
                  RETURNED, never that a push ran or succeeded.
                  `interrupted: true` is said only when the payload says so.
  review-invoked  review_runner_invoked(command) (bash_guard.py) names a
                  reviewer runner and the segment is not a setup probe
                  (`HJW_PROBE=` before the command word: no line, no
                  claim); the tool_use_id is claimed in the session list
                  `review_ids` (cap 64, newest kept), one line per call.
                  RUNNER_NOTE says what the detection is: command syntax
                  only — not that the runner ran, finished or reviewed
                  anything (2.34.0, debate 6; no exit status: that payload
                  field is unverified).
Both on one call: push-returned first, joined by a newline. Each emission
is observed (`hook: post_bash, decision: note, via, chars`).

Silent on a failed claim (diff review D4): a line is emitted only after its
claim was SAVED — a lock not taken, a failed save or an exception
means no line (the one-line-per-call bound cannot be established).

Exemptions (exit 0, no output — fail open, P4): malformed payload; subagent
payload; HAEJWO_GATE=off; a Codex host (unmeasured); config.json malformed;
`gate.enabled` or `gate.bash_guard` not true; a repeated tool_use_id. A
failed tool call (PostToolUseFailure) gets no line: that path is unmeasured
and not hooked.

Scope: one line per push-bearing call the bash guard recorded and one per
detected runner invocation; no state is written except the id lists
(saved without touching `updated_at`, so turn freshness —
gate.py's stale reset — never moves).
Origin: field round 3, root-cause consensus 2026-10-26: fact-based
re-presentation; measured on Claude Code 2.1.310 headless.
"""
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    SESSION_IDS_CAP, claim_session_id, emit_context, gate_disabled_by_env,
    is_subagent, load_config_with_status, load_state, observe, on_codex_host,
    paths, read_payload, sanitize_quoted, save_state, state_lock,
)
from bash_guard import review_runner_invoked  # noqa: E402

PUSH_RETURNED = (
    "[haejwo] push-bearing Bash call returned (tool call {id}); quoted shell "
    "output tail: \"{tail}\"{interrupted}. The Outward actions line states "
    "repo; branch; action; result and the authorizing words."
)
RUNNER_NOTE = (
    "[haejwo] detected runner call ({runner}; tool call {id}): command syntax "
    "only — whether it ran, finished or reviewed anything is not established. "
    "When its reply is read: decide each material finding fact vs remedy, "
    "with reasons; carry the closing statement (scope, verification, "
    "omissions) into the report; a same-model review is not independent."
)


def output_tail(response):
    """The last line of stderr, else of stdout, that is non-empty after
    sanitizing, else "(no output)"."""
    if isinstance(response, dict):
        for key in ("stderr", "stdout"):
            text = response.get(key)
            if not isinstance(text, str):
                continue
            for line in reversed(text.splitlines()):
                clean = sanitize_quoted(line)
                if clean:
                    return clean
    return "(no output)"


def _str_list(value):
    return [x for x in value if isinstance(x, str)] \
        if isinstance(value, list) else None


def claim_push_return(payload, data, sid, tuid):
    """True when `tuid` is a push-bearing call recorded this turn and this
    call claimed (and saved) its one return note; False otherwise —
    including a lock not taken, a failed save or any exception. Never
    raises."""
    if not tuid:
        return False
    try:
        with state_lock(data, sid) as lk:
            if not lk.acquired:
                return False
            state = load_state(data, sid)
            pid = payload.get("prompt_id") or payload.get("turn_id")
            if pid and state.get("prompt_id") != pid:
                return False  # the recorded ids belong to another turn
            seen = _str_list(state.get("outward_seen"))
            if seen is None:
                seen = _str_list(state.get("outward_ids")) or []
            claimed = _str_list(state.get("push_returned_ids")) or []
            if tuid not in seen or tuid in claimed \
                    or len(claimed) >= SESSION_IDS_CAP:
                return False
            state["push_returned_ids"] = claimed + [tuid]
            return save_state(data, sid, state, touch=False) is None
    except Exception:
        return False


def push_line(payload, data, sid, tuid):
    if not claim_push_return(payload, data, sid, tuid):
        return None
    response = payload.get("tool_response")
    interrupted = (isinstance(response, dict)
                   and response.get("interrupted") is True)
    return PUSH_RETURNED.format(
        id=tuid, tail=output_tail(response),
        interrupted="; interrupted: true" if interrupted else "")


def main():
    os.umask(0o077)  # cold-loop F15: state files are per-user
    payload = read_payload()
    if not isinstance(payload, dict) or not payload or is_subagent(payload):
        sys.exit(0)
    if gate_disabled_by_env():
        sys.exit(0)
    root, data = paths(sys.argv)
    if on_codex_host(root, data):
        sys.exit(0)
    cfg, status = load_config_with_status(data)
    gate = cfg.get("gate") or {}
    if status == "malformed" or gate.get("enabled") is not True \
            or gate.get("bash_guard") is not True:
        sys.exit(0)
    sid = payload.get("session_id", "unknown")
    tuid = payload.get("tool_use_id")
    tuid = tuid if isinstance(tuid, str) and tuid else None
    command = (payload.get("tool_input") or {}).get("command")
    command = command if isinstance(command, str) else ""

    lines = []
    line = push_line(payload, data, sid, tuid)
    if line:
        lines.append(("push-returned", line))
    runner = review_runner_invoked(command) if command else None
    if runner and claim_session_id(data, sid, "review_ids", tuid):
        lines.append(("review-invoked", RUNNER_NOTE.format(
            runner=runner, id=tuid or "id unknown")))
    if not lines:
        sys.exit(0)
    for via, text in lines:
        try:  # never load-bearing
            observe(data, {"v": 2, "hook": "post_bash", "decision": "note",
                           "via": via, "chars": len(text),
                           "sid": str(sid)[:12]})
        except Exception:
            pass
    emit_context("PostToolUse", "\n".join(text for _, text in lines))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
