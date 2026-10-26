#!/usr/bin/env python3
"""haejwo hand-back nudge — PostToolUse on Task|Agent.

Mechanism: when a main-agent Task/Agent call returns with
`tool_response.status == "completed"` (the measured value), this hook
re-presents the hand-back obligations next to the facts of that call as
additionalContext (a note: never a permission decision, never a block):
agent type (`tool_input.subagent_type`, else `tool_response.agentType`,
else "agent"), resolved model (`tool_response.resolvedModel`, printed as
observed and never as an independence claim, else "model unverified"),
both sanitized and cut to 60 chars (hjw_common.sanitize_quoted), tool-use
count (`tool_response.totalToolUseCount`, else "?") and status.
The session counter `handbacks` is bumped; a tool_use_id already in
`handback_ids` (cap 64, newest kept) is a duplicate event and gets no line.
Silent on a failed claim (diff review D4): the line is emitted only after
the bump was SAVED — a lock not taken, a failed save or an exception means
no line. The save keeps `updated_at` (turn freshness never moves, D1). Each
emission is observed (`hook: handback_nudge, decision: note, via:
completed, chars`).

Exemptions (exit 0, no output, no observation — fail open, P4): malformed
payload; subagent payload; HAEJWO_GATE=off; a Codex host (unmeasured);
config.json malformed; `gate.enabled` or `gate.delegation_guard` not true;
`tool_response` not an object or any status other than "completed" (e.g.
a background launch: unmeasured); a repeated tool_use_id; a claim not
saved.

Scope: one line per completed hand-back tool call. SubagentStop is never
used: its output goes to the subagent and replaces its real result (F5).
Origin: field round 3, root-cause consensus 2026-10-26: fact-based
re-presentation; measured on Claude Code 2.1.310 headless.
"""
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    bump_session_counter, emit_context, gate_disabled_by_env, is_subagent,
    load_config_with_status, observe, on_codex_host, paths, read_payload,
    sanitize_quoted,
)

HANDBACK = (
    "[haejwo] hand-back: {agent_type} ({model}; {n} tool uses; completed) "
    "returned. Decide each material finding fact vs remedy, with reasons; "
    "label its numbers worker-run; accept only diffs tracing to the brief or "
    "a disclosed judgment call."
)


def _text(value, limit=60):
    """A non-empty sanitized string, or None."""
    if isinstance(value, str):
        return sanitize_quoted(value, limit) or None
    return None


def render(tool_input, response):
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    n = response.get("totalToolUseCount")
    return HANDBACK.format(
        agent_type=(_text(tool_input.get("subagent_type"))
                    or _text(response.get("agentType")) or "agent"),
        model=_text(response.get("resolvedModel")) or "model unverified",
        n=n if isinstance(n, int) and not isinstance(n, bool) else "?")


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
            or gate.get("delegation_guard") is not True:
        sys.exit(0)
    response = payload.get("tool_response")
    if not isinstance(response, dict) or response.get("status") != "completed":
        sys.exit(0)
    sid = payload.get("session_id", "unknown")
    if not bump_session_counter(data, sid, "handbacks",
                                payload.get("tool_use_id"), "handback_ids"):
        sys.exit(0)  # a duplicate, or the claim was not saved: no line
    text = render(payload.get("tool_input"), response)
    try:  # never load-bearing
        observe(data, {"v": 2, "hook": "handback_nudge", "decision": "note",
                       "via": "completed", "chars": len(text),
                       "sid": str(sid)[:12]})
    except Exception:
        pass
    emit_context("PostToolUse", text)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
