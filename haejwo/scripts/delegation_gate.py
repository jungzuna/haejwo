#!/usr/bin/env python3
"""haejwo delegation gate — PreToolUse on Task|Agent.

The ONLY thing this denies: delegating to a KNOWN GENERIC agent
(general-purpose / Explore) with no explicit model override. With no model,
that agent INHERITS the session model — judgment-tier capability silently
spent on execution work, the exact leak the tiered subagents exist to avoid.
Everything else (unknown subagent_type, haejwo:* tiered workers, missing
fields, parse errors, subagent calls) is allowed — this is a delegation
gate, not a security boundary; fail open on any ambiguity.

Envelope field semantics (v2) — one line per field:
  v                — envelope schema version; bump only on incompatible
                     field changes, never for additive ones.
  hook             — always "delegation"; distinguishes these records from
                     gate/bash_guard records in observations.jsonl.
  subagent_type    — the requested delegation target, verbatim from
                     tool_input (may be null or a non-string).
  requested_model  — the explicit model override requested, or null if
                     none/blank/"inherit" (all treated as non-explicit).
                     Same normalization the decision itself uses, so this
                     field can never disagree with what was enforced.
  plan_marker_kind — "plan" | "no_plan" | "none": which plan marker (if
                     any) the prompt text carries, checked in that order —
                     "Plan:" wins over "No plan because" if both appear.
  prompt_bytes     — UTF-8 byte length of the prompt text; a cheap size
                     proxy for spotting feature-scale-looking briefs.
  decision         — "allow" | "deny": the final PreToolUse decision this
                     hook actually emitted (recorded once, after deciding).
  agent_type       — non-null only when this call originates inside a
                     subagent (the depth-1 exemption check).
  agent_id         — the subagent's id, present alongside agent_type; null
                     for the main agent.
  sid              — session_id truncated to 12 chars, to correlate
                     records without carrying a full session identifier.

Envelope derivation (marker/byte-count helpers, the observe() call itself)
is fail-open exactly like the decision path: a non-string or malformed-
Unicode prompt, or an observation failure, can never raise past this hook —
the already-computed decision still gets emitted.

Extending this envelope or adding event kinds requires stating why existing
fields don't fit (justify-before-extend).
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    allow, deny, gate_disabled_by_env, is_subagent, load_config, observe,
    paths, read_payload,
)

KNOWN_GENERIC = {"general-purpose", "Explore"}


def _normalize_model(model):
    """Single source of truth for "explicit model override" semantics — used
    by BOTH the decision path and the envelope record, so the audit trail can
    never disagree with what was actually enforced. None for absent/
    non-string/blank/"inherit" (any case) input — all of which inherit the
    session model just like an omitted model would; otherwise the original
    string value unchanged."""
    if not isinstance(model, str):
        return None
    m = model.strip()
    if not m or m.lower() == "inherit":
        return None
    return model


def _plan_marker_kind(prompt):
    """Never raises: a str() guard means a non-string prompt (None, dict,
    number, ...) short-circuits to "none" rather than being stringified and
    substring-matched. Precedence: "Plan:" wins over "No plan because" when
    a prompt somehow carries both."""
    if not isinstance(prompt, str):
        return "none"
    if "Plan:" in prompt:
        return "plan"
    if "No plan because" in prompt:
        return "no_plan"
    return "none"


def _prompt_bytes(prompt):
    """UTF-8 byte length of the prompt text — a cheap size proxy. Never
    raises: a str() guard sends non-string input straight to 0, and the
    encode() itself tolerates malformed Unicode (e.g. a lone surrogate) via
    errors="replace" inside a try/except that also collapses to 0."""
    if not isinstance(prompt, str):
        return 0
    try:
        return len(prompt.encode("utf-8", errors="replace"))
    except Exception:
        return 0


def main():
    payload = read_payload()
    if not payload:
        allow()

    root, data = paths(sys.argv)

    tool_input = payload.get("tool_input") or {}
    subagent_type = tool_input.get("subagent_type")
    model = tool_input.get("model")
    prompt = tool_input.get("prompt")

    # Normalized ONCE, shared by the decision and the envelope record below —
    # the audit trail can never show a requested_model that implies a
    # different decision than the one actually enforced.
    requested_model = _normalize_model(model)

    # Compute the final decision BEFORE observing, so the audit record is
    # written exactly ONCE with the outcome it actually produced (v1 wrote
    # the request only, in a separate emit from the eventual allow/deny).
    decision = "allow"
    deny_reason = None
    try:
        if not is_subagent(payload) and not gate_disabled_by_env():
            cfg = load_config(data)
            if cfg["gate"]["enabled"] and cfg["gate"]["delegation_guard"]:
                if subagent_type in KNOWN_GENERIC and requested_model is None:
                    decision = "deny"
                    deny_reason = (
                        f"[haejwo gate] Delegation to generic agent '{subagent_type}' without an "
                        f"explicit model — it would INHERIT the session model (judgment rates for "
                        f"execution). Pass model: 'haiku' (locate) or 'sonnet' (read/summarize), or "
                        f"delegate to haejwo:default-worker / haejwo:task-worker instead. Emergency "
                        f"override: /haejwo:gate off."
                    )
    except Exception:
        # Any ambiguity in the decision path fails open — still record it.
        decision = "allow"
        deny_reason = None

    # Envelope derivation must be exactly as fail-open as the decision path
    # above: a malformed prompt, an encoding surprise, or an observe()
    # failure must never prevent the already-computed decision from being
    # emitted below.
    try:
        observe(data, {
            "v": 2,
            "hook": "delegation",
            "subagent_type": subagent_type,
            "requested_model": requested_model,
            "plan_marker_kind": _plan_marker_kind(prompt),
            "prompt_bytes": _prompt_bytes(prompt),
            "decision": decision,
            "agent_type": payload.get("agent_type"),
            "agent_id": payload.get("agent_id"),
            "sid": str(payload.get("session_id"))[:12],
        })
    except Exception:
        pass

    if decision == "deny":
        deny(deny_reason)
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open, never brick the session
