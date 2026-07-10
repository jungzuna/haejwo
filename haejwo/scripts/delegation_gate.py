#!/usr/bin/env python3
"""haejwo delegation gate — PreToolUse on Task|Agent.

The ONLY thing this denies: delegating to a KNOWN GENERIC agent
(general-purpose / Explore) with no explicit model override. With no model,
that agent INHERITS the session model — judgment-tier capability silently
spent on execution work, the exact leak the tiered subagents exist to avoid.
Everything else (unknown subagent_type, haejwo:* tiered workers, missing
fields, parse errors, subagent calls) is allowed — this is a delegation
gate, not a security boundary; fail open on any ambiguity.
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    allow, deny, gate_disabled_by_env, is_subagent, load_config, observe,
    paths, read_payload,
)

KNOWN_GENERIC = {"general-purpose", "Explore"}


def _explicit_model(model):
    """True only for a real model override. None, "", whitespace-only, or
    "inherit" (any case) are all non-explicit — they inherit the session
    model just like an omitted model would."""
    if not isinstance(model, str):
        return False
    m = model.strip()
    return bool(m) and m.lower() != "inherit"


def main():
    payload = read_payload()
    if not payload:
        allow()

    root, data = paths(sys.argv)

    tool_input = payload.get("tool_input") or {}
    # Audit record: the REQUEST side of the contract. The effective model is
    # unknowable at PreToolUse (host resolves inheritance after this hook
    # runs), so this envelope records what was asked for, not what runs.
    observe(data, {
        "v": 1,
        "hook": "delegation",
        "subagent_type": tool_input.get("subagent_type"),
        "requested_model": tool_input.get("model") or None,
        "agent_type": payload.get("agent_type"),
        "agent_id": payload.get("agent_id"),
        "sid": str(payload.get("session_id"))[:12],
    })

    if is_subagent(payload):
        allow()  # depth-1 delegation isn't enforced here
    if gate_disabled_by_env():
        allow()

    cfg = load_config(data)
    if not (cfg["gate"]["enabled"] and cfg["gate"]["delegation_guard"]):
        allow()

    subagent_type = tool_input.get("subagent_type")
    model = tool_input.get("model")

    if subagent_type in KNOWN_GENERIC and not _explicit_model(model):
        deny(
            f"[haejwo gate] Delegation to generic agent '{subagent_type}' without an "
            f"explicit model — it would INHERIT the session model (judgment rates for "
            f"execution). Pass model: 'haiku' (locate) or 'sonnet' (read/summarize), or "
            f"delegate to haejwo:default-worker / haejwo:task-worker instead. Emergency "
            f"override: /haejwo:gate off."
        )

    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open, never brick the session
