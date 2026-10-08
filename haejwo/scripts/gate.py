#!/usr/bin/env python3
"""haejwo orchestration gate — PreToolUse on Edit|Write|NotebookEdit.

The MAIN agent may touch at most N DISTINCT code files per user turn
(default 2). The N+1th distinct code file is denied, and the deny reason
tells the model to delegate to the tiered subagents instead. Re-editing an
already-touched file stays free (iterating on one file is fine).
Subagents are exempt (agent_id/agent_type present in the payload).

Observation record (v2): the decision is computed FIRST, then observed
exactly once (outside the state lock), then emitted — so the audit trail can
never claim an outcome the hook didn't produce, and a slow/blocked
observations lock can never delay the decision while holding the session
lock. `via` is the reason the decision was reached, in precedence order:
  subagent-exempt  the call came from inside a subagent (never gated)
  env-off          HAEJWO_GATE=off in the environment
  config-malformed config.json is unparseable — allowed, gates fail open
  gate-off         config gate.enabled is false
  non-code         no code file in this call (non-code/temp/metadata paths)
  free-reedit      every file was already counted this turn (iteration free)
  budget-full      allowed, and this call filled the budget (warning context)
  ok               allowed with budget left
  budget           DENIED: the change would exceed the per-turn budget
  lock-unavailable the session lock was not taken within 2 s — allowed
                   WITHOUT reading or writing state (hjw_common.state_lock)
  fail-open        an internal error — allowed, as always (P4)
A call whose bookkeeping did not land (lock-unavailable, or save_state
failed) says so in its additionalContext on EVERY call
(hjw_common.state_not_persisted_note); `via` is unchanged by a save failure.
`offending` (deny only) lists the canonical paths that would have exceeded
the budget, capped at 6 — the same paths the deny text names.
"""
import os
import sys

import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    allow, canonical, carry_session_flags, command_name,
    config_ignored_note_once, deny, gate_disabled_by_env, is_code_file,
    is_subagent, load_config_with_status, load_state, malformed_note_once,
    observe, on_codex_host, paths, read_payload, save_state, state_lock,
    state_not_persisted_note, with_note,
)

STALE_TURN_SECONDS = 7200  # fallback reset if both turn signals ever fail


def _decide(payload, data, root=""):
    """Compute (decision, via, context, reason, offending) without emitting.

    Returns the allow/deny plus its observation fields; the caller observes once and
    then emits. Every `return` inside the state_lock block releases the lock
    on the way out, so the observation never happens while it's held.
    """
    if is_subagent(payload):
        return "allow", "subagent-exempt", None, None, None  # workers are the point
    if gate_disabled_by_env():
        return "allow", "env-off", None, None, None

    cfg, cfg_status = load_config_with_status(data)
    if cfg_status == "malformed":
        # P4, origin 2026-09-21 audit item 1: a config.json we could not parse
        # must never DENY — enforcing a budget we could not read would brick a
        # session over a stray comma. Allow, and say so ONCE, so the silence
        # is not mistaken for a gate that is still working. The note is shared
        # across all three enforcement hooks (hjw_common.malformed_note_once):
        # whichever fires first emits it. An ABSENT config keeps the
        # DEFAULT_CONFIG behavior (there is nothing broken to fix).
        note = malformed_note_once(
            data, payload.get("session_id", "unknown"),
            payload.get("tool_name") == "apply_patch" or on_codex_host(root, data))
        return "allow", "config-malformed", note, None, None
    # cold-loop B2: a wrong-typed value was replaced by its default at load;
    # say so once per session, on whatever this call emits.
    note = config_ignored_note_once(
        data, payload.get("session_id", "unknown"), cfg)
    decision, via, context, reason, offending = _decide_loaded(
        payload, data, root, cfg)
    context, reason = with_note(note, context, reason)
    return decision, via, context, reason, offending


def _decide_loaded(payload, data, root, cfg):
    """_decide past the config load: the gate and budget decision proper."""
    if not cfg["gate"]["enabled"]:
        return "allow", "gate-off", None, None, None

    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd", "")

    # Host adapter: Claude edits carry one file per call (file_path /
    # notebook_path); Codex batches edits in ONE apply_patch whose file
    # paths live inside tool_input.command (format:
    # "*** Add|Update|Delete File: <path>"). Delete counts too — destructive.
    # An Update hunk may rename with "*** Move to: <path>": the destination
    # is a file this change writes, so it counts as well (cold-loop B1).
    on_codex = (payload.get("tool_name") == "apply_patch"
                or on_codex_host(root, data))
    if payload.get("tool_name") == "apply_patch":
        import re
        raw_paths = re.findall(
            r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$",
            str(tool_input.get("command") or ""), re.M)
    else:
        raw_paths = [tool_input.get("file_path")
                     or tool_input.get("notebook_path") or ""]

    new_paths = []
    seen = set()
    for p in raw_paths:
        if p and is_code_file(p, cfg, cwd):
            cp = canonical(p, cwd)
            if cp not in seen:
                seen.add(cp)
                new_paths.append(cp)
    if not new_paths:
        return "allow", "non-code", None, None, None

    sid = payload.get("session_id", "unknown")
    max_files = int(cfg["gate"]["max_files_per_turn"])

    # Lock so concurrent tool calls can't both slip under the budget
    # (prevents a read-check-write undercount race).
    with state_lock(data, sid) as lk:
        if not lk.acquired:
            # P4 + cold-loop D10: never an unlocked read/check/write — allow
            # untouched, and say the budget did not count this call.
            return ("allow", "lock-unavailable",
                    state_not_persisted_note(lk.error), None, None)
        state = load_state(data, sid)

        # Turn boundary, belt & braces: prompt_id (Claude) / turn_id (Codex)
        # change (lazy) OR the UserPromptSubmit reset hook, plus a stale
        # fallback if both fail.
        # Session-scoped once-note flags survive both resets
        # (hjw_common.carry_session_flags).
        pid = payload.get("prompt_id") or payload.get("turn_id")
        if pid and state.get("prompt_id") != pid:
            state = carry_session_flags(state, {"prompt_id": pid, "files": []})
        elif state.get("updated_at") and time.time() - state["updated_at"] > STALE_TURN_SECONDS:
            state = carry_session_flags(state, {"prompt_id": pid, "files": []})

        files = state.get("files", [])
        additions = [p for p in new_paths if p not in files]

        if not additions:
            # every file already touched this turn — iteration is free
            return "allow", "free-reedit", None, None, None

        # Whole-change decision: apply_patch is atomic at this layer, so a
        # multi-file patch that would exceed the budget is denied entirely.
        if len(files) + len(additions) > max_files:
            listed = ", ".join(files[:6]) or "none"
            # Codex has no Agent tool; its delegation primitive is
            # spawn_agent (cold-loop D7), and its commands are skills
            # (F11). The Claude text stays as it was.
            delegate_via = "spawn_agent" if on_codex else "the Agent tool"
            offending = ", ".join(additions[:6])
            reason = (
                f"[haejwo gate] Per-turn code-edit budget exceeded: this change adds "
                f"{len(additions)} new file(s) ({offending}) on top of {len(files)}/"
                f"{max_files} already touched ({listed}). Do NOT edit more code files "
                f"directly — split the change or delegate via {delegate_via}: "
                f"'haejwo:default-worker' (implementation), 'haejwo:task-worker' "
                f"(mechanical chores), 'haejwo:deep-reasoner' (hard design/analysis). "
                f"Re-editing the files already touched this turn is still allowed. "
                f"If this is unplanned feature-scale work, run "
                f"{command_name('plan', on_codex)} first "
                f"(backup nudge — plan-first is the norm for delegate-tier work). "
                f"Emergency override: {command_name('gate off', on_codex)}."
            )
            return "deny", "budget", None, reason, additions[:6]

        files.extend(additions)
        state["files"] = files
        save_err = save_state(data, sid, state)
        budget_full = len(files) == max_files

    # cold-loop D15: a failed save is said on EVERY call, never via a
    # once-note (that flag would live in the same unwritable state).
    not_saved = state_not_persisted_note(save_err) if save_err else None
    if budget_full:
        context = (
            f"[haejwo gate] Edit budget now full ({len(files)}/{max_files} distinct code "
            f"files this turn). Any FURTHER code file this turn must be delegated to a "
            f"subagent (haejwo:default-worker / haejwo:task-worker)."
        )
        if not_saved:
            context += "\n" + not_saved
        return "allow", "budget-full", context, None, None
    return "allow", "ok", not_saved, None, None


def main():
    os.umask(0o077)  # cold-loop F15: state and observations are per-user
    payload = read_payload()
    if not payload:
        allow()

    root, data = paths(sys.argv)

    # Audit record: who fired, which file, and what was decided (an audit
    # trail of hook activity; also proves whether hooks fire in subagents).
    # Built defensively: a malformed payload/tool_input must still produce a
    # fail-open RECORD, not a silent exit through the outer handler.
    record = {"v": 2, "hook": "gate"}
    try:
        _ti = payload.get("tool_input") or {}
        record.update({
            "tool": payload.get("tool_name"),
            "path": _ti.get("file_path") or _ti.get("notebook_path") or "",
            "agent_type": payload.get("agent_type"),
            "agent_id": payload.get("agent_id"),
            "sid": str(payload.get("session_id"))[:12],
        })
    except Exception:
        pass

    try:
        decision, via, context, reason, offending = _decide(payload, data, root)
    except Exception:
        decision, via, context, reason, offending = "allow", "fail-open", None, None, None

    record["decision"] = decision
    record["via"] = via
    if offending:
        record["offending"] = offending[:6]
    # Observed OUTSIDE the state lock, and never load-bearing: an observation
    # failure (or a busy observations lock) must never change, block, or
    # delay the decision.
    try:
        observe(data, record)
    except Exception:
        pass

    if decision == "deny":
        deny(reason)
    allow(context)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open, never brick the session
