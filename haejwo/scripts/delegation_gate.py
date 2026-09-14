#!/usr/bin/env python3
"""haejwo delegation gate — PreToolUse on Task|Agent.

Two things are denied, and only these two:
 1. delegating to a KNOWN GENERIC agent (general-purpose / Explore) with no
    explicit model override. With no model, that agent INHERITS the session
    model — judgment-tier capability silently spent on execution work, the
    exact leak the tiered subagents exist to avoid.
 2. delegating to a haejwo TIER worker with no model override while the
    user's config pins a model for that tier that DIFFERS from the agent
    file's own default — omission would silently run the cheaper agent-file
    default instead of the configured pin (tier pin check, below).
Everything else (unknown subagent_type, missing fields, parse errors,
subagent calls) is allowed — this is a delegation gate, not a security
boundary; fail open on any ambiguity.

Tier pin check (origin 2026-08-21 silent-downgrade): applies ONLY to the
haejwo tier workers (prefixed or bare names). It denies only when ALL hold:
no explicit model was requested, the configured pin for that tier is an
explicit model (not blank/"inherit"), and that pin differs from the agent
file's frontmatter default. An unreadable config or unreadable/malformed
frontmatter SKIPS the check (never deny on state we could not read).

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
  tier_pin_check   — why the tier pin check decided what it did (additive
                     field; existing fields unchanged):
                     "deny"                      pin differs from the agent
                                                 file default, model omitted
                     "pass:explicit-model"       an explicit model was passed
                     "pass:pin-matches-default"  pin == agent file default
                     "pass:pin-inherit"          no explicit pin configured
                     "pass:not-a-tier"           the check did not apply:
                                                 subagent_type is not a tier
                                                 (or not a string), the gate/
                                                 guard is off, the call came
                                                 from a subagent, or the host
                                                 is Codex (whose spawn_agent
                                                 never hits this matcher)
                     "skip:config-unreadable"    config.json is malformed
                     "skip:frontmatter-unreadable" agent file missing or its
                                                 frontmatter is malformed
                     "skip:fail-open"            an exception in the decision
                                                 path (allowed, as always)
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
import os
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    allow, deny, gate_disabled_by_env, is_subagent, load_config_with_status,
    observe, paths, read_payload,
)

KNOWN_GENERIC = {"general-purpose", "Explore"}

# haejwo tier workers -> (agent file basename, config models.<role> key).
# Bare names are accepted too: hosts have been observed passing either the
# plugin-prefixed or the bare agent name.
TIER_AGENTS = {
    "haejwo:default-worker": ("default-worker", "default_worker"),
    "haejwo:task-worker": ("task-worker", "task_worker"),
    "haejwo:deep-reasoner": ("deep-reasoner", "deep_reasoner"),
    "default-worker": ("default-worker", "default_worker"),
    "task-worker": ("task-worker", "task_worker"),
    "deep-reasoner": ("deep-reasoner", "deep_reasoner"),
}


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


CLAUDE_TIER_ONLY = (
    "Delegate to haejwo:default-worker / haejwo:task-worker instead "
    "(on Claude, omission uses each agent file's default model)."
)
CODEX_TIER_ONLY = (
    "Delegate to haejwo:default-worker / haejwo:task-worker instead "
    "(configured tiers inherit — never pass model:'inherit')."
)


def _next_action(cfg, on_codex):
    """Build the "next action" deny clause from the CONFIGURED tiers, so the
    steering names models the user actually pinned instead of hard-coded
    aliases (origin 2026-09-14: a Claude user on custom pins was told to pass
    'haiku'/'sonnet', which their config never mentions). _normalize_model
    keeps "explicit" meaning the same thing here as everywhere else.

    Claude host: name whichever tiers are explicit (with the default config
    sonnet/haiku this stays byte-identical to the long-tested wording); when
    neither is, recommend the tiers only. Codex host: name both only when
    BOTH are explicit — naming a non-explicit one would recommend
    model:'inherit', which this very gate would re-deny.
    Raises on a malformed models/models_codex value; the caller catches that
    and falls back to the host-appropriate tier-only wording."""
    m = cfg["models_codex" if on_codex else "models"]
    task_worker = _normalize_model(m.get("task_worker"))
    default_worker = _normalize_model(m.get("default_worker"))
    if task_worker and default_worker:
        if task_worker == default_worker:
            # both tiers on one model: naming it twice with two role labels
            # reads like a choice the user does not actually have
            return (
                f"Pass model: '{task_worker}', or delegate to "
                f"haejwo:default-worker / haejwo:task-worker instead."
            )
        return (
            f"Pass model: '{task_worker}' (locate) or '{default_worker}' "
            f"(read/summarize), or delegate to haejwo:default-worker / "
            f"haejwo:task-worker instead."
        )
    if on_codex:
        return CODEX_TIER_ONLY
    if default_worker:
        return (
            f"Pass model: '{default_worker}' (read/summarize), or delegate "
            f"to haejwo:default-worker / haejwo:task-worker instead."
        )
    if task_worker:
        return (
            f"Pass model: '{task_worker}' (locate), or delegate to "
            f"haejwo:default-worker / haejwo:task-worker instead."
        )
    return CLAUDE_TIER_ONLY


_FM_KEY = re.compile(r"^([A-Za-z0-9_.-]+):(.*)$")
_MODEL_VALUE = re.compile(r"^[A-Za-z0-9._-]+$")
# YAML flow/block indicators this deliberately dumb parser will not guess at.
_UNPARSEABLE_STARTS = ("[", "{", "|", ">")


def _fm_value(raw):
    """Scalar value of a frontmatter line: drop an inline comment (a `#`
    preceded by whitespace — a `#` inside a token is part of the token), one
    pair of matching surrounding quotes, and whitespace."""
    m = re.search(r"\s#", raw)
    if m:
        raw = raw[:m.start()]
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    return v


def _agent_file_default(root, agent_name):
    """The `model:` default declared in an agent file's YAML frontmatter.

    Returns the declared value, "inherit" for a valid frontmatter that
    declares no model (or an empty `model:` — the documented agents/*.md
    convention: omit `model` to inherit), or None when anything at all is
    uncertain. None means SKIP the tier pin check: this is a deliberately
    dumb parser, not a YAML implementation, and an UNCERTAIN read must never
    produce a deny (P4).

    Certainty rules (origin 2026-09-14 review): the block is delimited ONLY
    by lines equal to `---` at column 0 (trailing CR and a leading BOM
    tolerated) — an indented `---` inside a description does NOT close it;
    `model:` counts only as a column-0 key; a block line that is neither a
    column-0 `key: value` nor an indented continuation, a value opening a
    flow/block structure, and a model value outside [A-Za-z0-9._-] all mean
    "unreadable". So do a `model:` whose value continues on an indented line,
    a `key:value` with no space after the colon, and a `model:` declared
    twice — each would make the effective default a guess."""
    try:
        path = os.path.join(root or "", "agents", str(agent_name) + ".md")
        with open(path, encoding="utf-8-sig") as f:
            raw = f.read()
    except Exception:
        return None
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in raw.split("\n")]
    if lines and lines[0].startswith("\ufeff"):  # belt & braces next to utf-8-sig
        lines[0] = lines[0][1:]

    start = None
    for i, line in enumerate(lines):
        if line == "---":
            start = i
            break
        if line.strip():
            return None  # content before any frontmatter: no frontmatter
    if start is None:
        return None
    end = None
    for j in range(start + 1, len(lines)):
        if lines[j] == "---":
            end = j
            break
    if end is None:
        return None  # opening --- with no closing --- at column 0

    block = lines[start + 1:end]
    model = None
    for idx, line in enumerate(block):
        if not line.strip():
            continue
        if line[:1].isspace():
            continue  # indented continuation of the previous key
        m = _FM_KEY.match(line)
        if not m:
            return None  # not a column-0 `key: value` line
        key, raw = m.group(1), m.group(2)
        if raw and not raw[:1].isspace():
            return None  # `key:value` — not a mapping we will guess at
        value = _fm_value(raw)
        if value[:1] in _UNPARSEABLE_STARTS:
            return None
        if key == "model":
            if model is not None:
                return None  # declared twice: which one wins is a guess
            nxt = block[idx + 1] if idx + 1 < len(block) else ""
            if not value and nxt.strip() and nxt[:1].isspace():
                return None  # value continues on an indented line
            model = value
    if not model:
        return "inherit"  # no model key, or `model:` with an empty value
    if not _MODEL_VALUE.match(model):
        return None
    return model


def _tier_pin_check(subagent_type, model, requested_model, cfg, cfg_status, root,
                    on_codex):
    """Decide the tier pin check. Returns (result, file_default, pin) where
    result is one of the documented tier_pin_check values; only "deny" denies.
    Never raises — the caller's fail-open wrapper is the backstop, not the
    plan."""
    if on_codex:
        # Codex delegates via spawn_agent, which never matches this hook's
        # ^(Task|Agent)$ matcher — the branch cannot fire there, so never
        # deny a Codex call on a Claude-side agent-file default.
        return "pass:not-a-tier", None, None
    if not isinstance(subagent_type, str) or subagent_type not in TIER_AGENTS:
        return "pass:not-a-tier", None, None
    agent_name, role = TIER_AGENTS[subagent_type]
    if model is not None and not isinstance(model, str):
        # A model we cannot even read as a string: we have no idea what the
        # host asked for, so we cannot claim the pin was ignored. Fail open.
        # (The generic-agent check above keeps its own tested contract, where
        # a non-string model counts as no explicit model.)
        return "skip:fail-open", None, None
    if cfg_status == "malformed":
        return "skip:config-unreadable", None, None
    if requested_model is not None:
        return "pass:explicit-model", None, None
    models = cfg.get("models")
    pin = _normalize_model(models.get(role)) if isinstance(models, dict) else None
    if pin is None:
        return "pass:pin-inherit", None, None
    file_default = _agent_file_default(root, agent_name)
    if file_default is None:
        return "skip:frontmatter-unreadable", None, pin
    if file_default.strip() == pin.strip():
        return "pass:pin-matches-default", file_default, pin
    return "deny", file_default, pin


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
    tier_pin_check = "pass:not-a-tier"  # "the check did not apply" bucket
    try:
        if not is_subagent(payload) and not gate_disabled_by_env():
            # ONE read serves both the decision and the tier check: the two
            # can never disagree about what the config said.
            cfg, cfg_status = load_config_with_status(data)
            if cfg["gate"]["enabled"] and cfg["gate"]["delegation_guard"]:
                # Host detection by plugin path: codex passes compat env/argv
                # rooted under /.codex/plugins (measured heuristic, same test
                # session_brief.py:87 uses to pick codex vs Claude wording).
                on_codex = "/.codex/" in (root or "") or "/.codex/" in (data or "")
                if subagent_type in KNOWN_GENERIC and requested_model is None:
                    decision = "deny"
                    try:
                        next_action = _next_action(cfg, on_codex)
                    except Exception:
                        # Fail open to the host-appropriate tier-only
                        # wording, NOT to "allow" — this is still a deny,
                        # just without naming unreadable model pins.
                        next_action = CODEX_TIER_ONLY if on_codex else CLAUDE_TIER_ONLY
                    deny_reason = (
                        f"[haejwo gate] Delegation to generic agent '{subagent_type}' without an "
                        f"explicit model — it would INHERIT the session model (judgment rates for "
                        f"execution). {next_action} Emergency override: /haejwo:gate off."
                    )
                else:
                    # Tier pin check — disjoint from the generic-agent check
                    # above by construction (a tier name is never a KNOWN_
                    # GENERIC name), so the two can never fire on one call.
                    tier_pin_check, file_default, pin = _tier_pin_check(
                        subagent_type, model, requested_model, cfg, cfg_status,
                        root, on_codex)
                    if tier_pin_check == "deny":
                        decision = "deny"
                        deny_reason = (
                            f"[haejwo gate] Delegation to '{subagent_type}' without a model "
                            f"override — the agent file defaults to '{file_default}' but your "
                            f"config pins '{pin}' for this tier (omission would not honor the "
                            f"pin; origin 2026-08-21 silent-downgrade). Pass model: '{pin}', or "
                            f"another explicit model if you intend to override the pin, or run "
                            f"/haejwo:setup to change it. Emergency override: /haejwo:gate off."
                        )
    except Exception:
        # Any ambiguity in the decision path fails open — still record it.
        decision = "allow"
        deny_reason = None
        tier_pin_check = "skip:fail-open"

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
            "tier_pin_check": tier_pin_check,
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
