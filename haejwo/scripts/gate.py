#!/usr/bin/env python3
"""haejwo orchestration gate — PreToolUse on Edit|Write|NotebookEdit.

The MAIN agent may touch at most N DISTINCT code files per user turn
(default 2). The N+1th distinct code file is denied, and the deny reason
tells the model to delegate to the tiered subagents instead. Re-editing an
already-touched file stays free (iterating on one file is fine).
Subagents are exempt (agent_id/agent_type present in the payload).
"""
import sys

import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    allow, canonical, deny, gate_disabled_by_env, is_code_file, is_subagent,
    load_config, load_state, observe, paths, read_payload, save_state,
    state_lock,
)

STALE_TURN_SECONDS = 7200  # fallback reset if both turn signals ever fail


def main():
    payload = read_payload()
    if not payload:
        allow()

    root, data = paths(sys.argv)

    # Audit record: who fired, and which file (an audit trail of hook
    # activity; also proves whether hooks fire inside subagents).
    _ti = payload.get("tool_input") or {}
    observe(data, {
        "hook": "gate",
        "tool": payload.get("tool_name"),
        "path": _ti.get("file_path") or _ti.get("notebook_path") or "",
        "agent_type": payload.get("agent_type"),
        "agent_id": payload.get("agent_id"),
        "sid": str(payload.get("session_id"))[:12],
    })

    if is_subagent(payload):
        allow()  # delegated workers are the point — never gate them
    if gate_disabled_by_env():
        allow()

    cfg = load_config(data)
    if not cfg["gate"]["enabled"]:
        allow()

    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd", "")

    # Host adapter: Claude edits carry one file per call (file_path /
    # notebook_path); Codex batches edits in ONE apply_patch whose file
    # paths live inside tool_input.command (format:
    # "*** Add|Update|Delete File: <path>"). Delete counts too — destructive.
    if payload.get("tool_name") == "apply_patch":
        import re
        raw_paths = re.findall(
            r"^\*\*\* (?:Add|Update|Delete) File: (.+)$",
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
        allow()

    sid = payload.get("session_id", "unknown")
    max_files = int(cfg["gate"]["max_files_per_turn"])

    # Lock so concurrent tool calls can't both slip under the budget
    # (prevents a read-check-write undercount race).
    with state_lock(data, sid):
        state = load_state(data, sid)

        # Turn boundary, belt & braces: prompt_id (Claude) / turn_id (Codex)
        # change (lazy) OR the UserPromptSubmit reset hook, plus a stale
        # fallback if both fail.
        pid = payload.get("prompt_id") or payload.get("turn_id")
        if pid and state.get("prompt_id") != pid:
            state = {"prompt_id": pid, "files": []}
        elif state.get("updated_at") and time.time() - state["updated_at"] > STALE_TURN_SECONDS:
            state = {"prompt_id": pid, "files": []}

        files = state.get("files", [])
        additions = [p for p in new_paths if p not in files]

        if not additions:
            allow()  # every file already touched this turn — iteration is free

        # Whole-change decision: apply_patch is atomic at this layer, so a
        # multi-file patch that would exceed the budget is denied entirely.
        if len(files) + len(additions) > max_files:
            listed = ", ".join(files[:6]) or "none"
            offending = ", ".join(additions[:6])
            deny(
                f"[haejwo gate] Per-turn code-edit budget exceeded: this change adds "
                f"{len(additions)} new file(s) ({offending}) on top of {len(files)}/"
                f"{max_files} already touched ({listed}). Do NOT edit more code files "
                f"directly — split the change or delegate via the Agent tool: "
                f"'haejwo:default-worker' (implementation), 'haejwo:task-worker' "
                f"(mechanical chores), 'haejwo:deep-reasoner' (hard design/analysis). "
                f"Re-editing the files already touched this turn is still allowed. "
                f"If this is unplanned feature-scale work, run /haejwo:plan first "
                f"(backup nudge — plan-first is the norm for delegate-tier work). "
                f"Emergency override: /haejwo:gate off."
            )

        files.extend(additions)
        state["files"] = files
        save_state(data, sid, state)

    if len(files) == max_files:
        allow(
            f"[haejwo gate] Edit budget now full ({len(files)}/{max_files} distinct code "
            f"files this turn). Any FURTHER code file this turn must be delegated to a "
            f"subagent (haejwo:default-worker / haejwo:task-worker)."
        )
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open, never brick the session
