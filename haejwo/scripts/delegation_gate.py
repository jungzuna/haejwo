#!/usr/bin/env python3
"""haejwo delegation gate — PreToolUse on Task|Agent.

Two things are denied, and only these two:
 1. delegating to a KNOWN GENERIC agent (general-purpose / Explore) with no
    explicit model override. With no model, that agent INHERITS the session
    model — judgment-tier capability silently spent on execution work, the
    exact leak the tiered subagents exist to avoid. Explore's model, per
    Claude Code docs (sub-agents, built-in subagents): under a Fable host the
    built-in Explore runs on the `opus` alias; otherwise it inherits the main
    conversation's model.
 2. delegating to a haejwo TIER worker with no model override while the
    user's config pins a model for that tier that DIFFERS from the agent
    file's own default — omission would silently run the agent-file default
    instead of the configured pin (tier pin check, below).
Everything else (unknown subagent_type, missing fields, parse errors,
subagent calls) is allowed — this is a delegation gate, not a security
boundary; fail open on any ambiguity.

Tier pin check (origin 2026-08-21 silent-downgrade): applies ONLY to the
haejwo tier workers (prefixed or bare names). It denies only when ALL hold:
no explicit model was requested, the configured pin for that tier is an
explicit model (not blank/"inherit"), that pin differs from the agent
file's frontmatter default, and the pin is PASSABLE — a member of the
MEASURED alias set hjw_common.PASSABLE_MODEL_ALIASES (sonnet/opus/haiku).
Compatibility boundary: that set is what the Claude Code Agent tool's
`model` accepted on 2026-10-02 (a full id is an InputValidationError); the
enum may also carry account-specific aliases (e.g. `fable`), which are NOT
in the set. Extend it only by measurement. Any pin outside the set (full
ids, unknown words, account-specific aliases) could not be relied on to
pass, and denying would brick that tier (P4) with a steer the host may not
be able to follow (P6): it is allowed as "skip:pin-not-passable" with a
once-per-session note. An unreadable config or unreadable/malformed
frontmatter SKIPS the check (never deny on state we could not read).

Envelope (v2, one observations.jsonl record per well-formed call; malformed
input exits before observing; written once after deciding; derivation and observe() fail open — the decision is emitted
regardless). Extending it requires stating why existing fields don't fit.
  v                — schema version; bump only on incompatible changes.
  hook             — always "delegation".
  subagent_type    — the requested target, verbatim (may be null/non-string).
  requested_model  — the explicit override, or null for none/blank/"inherit";
                     the same normalization the decision uses.
  plan_marker_kind — "plan" | "no_plan" | "none" (telemetry, see
                     _plan_marker_kind; "plan" wins when both appear).
  decision         — "allow" (not denied; no decision is emitted) | "deny".
  tier_pin_check   — "deny" | "pass:explicit-model" | "pass:pin-inherit" |
                     "pass:pin-matches-default" | "pass:not-a-tier" (not a
                     tier, gate/guard off, subagent call, or Codex host) |
                     "skip:config-malformed" (the whole hook fails open,
                     origin 2026-09-21 audit item 1) |
                     "skip:frontmatter-unreadable" | "skip:pin-not-passable"
                     (allowed, once-per-session note) | "skip:fail-open".
  agent_type       — non-null only inside a subagent (depth-1 exemption).
  agent_id         — the subagent's id alongside agent_type, else null.
  sid              — session_id truncated to 12 chars.
"""
import os
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    CODEX_SPAWN_WORKER, allow, command_name, config_ignored_note_once, deny,
    gate_disabled_by_env, is_subagent, load_config_with_status,
    PASSABLE_MODEL_ALIASES, load_state,
    malformed_note_once, observe, on_codex_host, paths, read_payload,
    save_state, state_lock, with_note,
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
    f"Delegate via {CODEX_SPAWN_WORKER} instead "
    "(configured tiers inherit — never pass model:'inherit')."
)
# The current role words (cycle 3 F8/D1), paired with the configured model.
TASK_ROLE = "bounded mechanical work"
DEFAULT_ROLE = "implementation"


def _next_action(cfg, on_codex):
    """Build the "next action" deny clause from the CONFIGURED tiers, so the
    steering names models the user actually pinned instead of hard-coded
    aliases (origin 2026-09-14: a Claude user on custom pins was told to pass
    'haiku'/'sonnet', which their config never mentions). _normalize_model
    keeps "explicit" meaning the same thing here as everywhere else.

    Claude host: name whichever tiers are explicit (the default config pins
    both to opus); when neither is, recommend the tiers only. Codex host:
    name both only when BOTH are explicit — naming a non-explicit one would recommend
    model:'inherit', which this very gate would re-deny.
    Raises on a malformed models/models_codex value; the caller catches that
    and falls back to the host-appropriate tier-only wording.
    On Codex the alternative is spawn_agent, never a haejwo agent name: the
    Codex manifest ships no agents (cycle 3 F4)."""
    m = cfg["models_codex" if on_codex else "models"]
    task_worker = _normalize_model(m.get("task_worker"))
    default_worker = _normalize_model(m.get("default_worker"))
    alt = (f"or use {CODEX_SPAWN_WORKER} instead." if on_codex else
           "or delegate to haejwo:default-worker / haejwo:task-worker instead.")
    if task_worker and default_worker:
        if task_worker == default_worker:
            # both tiers on one model: naming it twice with two role labels
            # reads like a choice the user does not actually have
            return f"Pass model: '{task_worker}', {alt}"
        return (
            f"Pass model: '{task_worker}' ({TASK_ROLE}) or '{default_worker}' "
            f"({DEFAULT_ROLE}), {alt}"
        )
    if on_codex:
        return CODEX_TIER_ONLY
    if default_worker:
        return f"Pass model: '{default_worker}' ({DEFAULT_ROLE}), {alt}"
    if task_worker:
        return f"Pass model: '{task_worker}' ({TASK_ROLE}), {alt}"
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
        # YAML comment: neither a key nor a continuation *[task-worker.md
        # carries a column-0 YAML comment (2026-09-21); comments are not
        # uncertainty]*
        if line.lstrip()[:1] == "#":
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
            if not value:
                # an empty `model:` may still be continued further down: skip
                # comment/blank lines before judging the next REAL line.
                for nxt in block[idx + 1:]:
                    if not nxt.strip() or nxt.lstrip()[:1] == "#":
                        continue
                    if nxt[:1].isspace():
                        return None  # value continues on an indented line
                    break
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
        return "skip:config-malformed", None, None  # backstop; main() short-circuits
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
    if pin.strip() not in PASSABLE_MODEL_ALIASES:
        return "skip:pin-not-passable", file_default, pin
    return "deny", file_default, pin


def _pin_not_passable_note_once(data_dir, session_id, role, pin, file_default,
                                on_codex=False):
    """The not-passable note, ONCE per session per role (same flag-in-
    session-state pattern as hjw_common.malformed_note_once; turn_reset
    preserves the flag). None on later calls. An unwritable state repeats the
    note — the fail-open direction."""
    note = (
        f"[haejwo] {role}: configured pin `{pin}` is not enforced; allowing "
        f"the agent-file default `{file_default}` (`{pin}` is outside the "
        f"measured passable aliases sonnet/opus/haiku). To enforce a pin, set "
        f"an alias in {command_name('setup', on_codex)}, or pass the model explicitly if your "
        f"session's Agent tool offers it."
    )
    try:
        with state_lock(data_dir, session_id) as lk:
            if not lk.acquired:
                return note  # bounded lock missed: no unlocked write; repeat
            state = load_state(data_dir, session_id)
            noted = state.get("pin_unpassable_noted")
            noted = list(noted) if isinstance(noted, list) else []
            if role in noted:
                return None
            state["pin_unpassable_noted"] = noted + [role]
            save_state(data_dir, session_id, state)
        return note
    except Exception:
        return note


# A plan marker is a LABEL AT THE HEAD OF A LINE, written the way markdown
# writes labels: `Plan:`, `**Plan**:`, `**Plan:**`, `- **Plan**: …`,
# `## Plan (합의본 — …)`, `Plan —`, or `Plan` alone on a line (origin
# 2026-09-28: a brief with the agreed plan under a heading recorded "none").
# Line-local: `[ \t]` never crosses a newline. Separators are `:`, `(`, a dash
# then space/EOL, or EOL — "Plan to investigate" is prose, `\b` keeps
# "Planning" out, and a bullet `[-*]` needs trailing whitespace so `**Plan**`
# is never read as one.
_MARKER_HEAD = r"^[ \t]*(?:[-*][ \t]+)?(?:\#{1,6}[ \t]*)?(?:\*\*[ \t]*)?"
# After the label: an optional closing `**` either side of the colon.
_MARKER_TAIL = (r"[ \t]*(?::[ \t]*(?:\*\*)?"
                r"|\*\*[ \t]*(?::|\(|[\u2014\u2013-](?=[ \t]|$)|$)"
                r"|\(|[\u2014\u2013-](?=[ \t]|$)|$)")
_PLAN_LABEL_RE = re.compile(_MARKER_HEAD + r"Plan\b" + _MARKER_TAIL, re.M)
_NO_PLAN_LABEL_RE = re.compile(
    _MARKER_HEAD + r"No[ \t]+plan[ \t]+because\b" + _MARKER_TAIL, re.M)


def _plan_marker_kind(prompt):
    """TELEMETRY ONLY — which marker the brief CARRIES, never that a plan
    was agreed. Never raises (a non-string prompt is "none"). The legacy
    substrings ("Plan:", "No plan because" anywhere) count alongside the
    label forms; "plan" wins when both kinds appear. CRLF/CR are normalized
    first (`$` would otherwise stop at `\r`). Known limitation, accepted: a
    marker inside a fenced code block counts too."""
    if not isinstance(prompt, str):
        return "none"
    text = prompt.replace("\r\n", "\n").replace("\r", "\n")
    if "Plan:" in text or _PLAN_LABEL_RE.search(text):
        return "plan"
    if "No plan because" in text or _NO_PLAN_LABEL_RE.search(text):
        return "no_plan"
    return "none"


def main():
    os.umask(0o077)  # cold-loop F15: state and observations are per-user
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
    context = None
    tier_pin_check = "pass:not-a-tier"  # "the check did not apply" bucket
    ignored_note = None
    try:
        if not is_subagent(payload) and not gate_disabled_by_env():
            # ONE read serves both the decision and the tier check: the two
            # can never disagree about what the config said.
            cfg, cfg_status = load_config_with_status(data)
            # cold-loop B2: wrong-typed values fell back to their defaults at
            # load (never set on a malformed file); said once per session,
            # on whatever this call emits.
            ignored_note = config_ignored_note_once(
                data, payload.get("session_id", "unknown"), cfg)
            if cfg_status == "malformed":
                # P4, origin 2026-09-21 audit item 1: an unparseable config
                # fails the WHOLE hook open. Not just the tier pin: the
                # generic-agent deny steers with model names read from that
                # same file, so denying here would name tiers the user may
                # never have configured. An ABSENT config keeps defaults.
                # The one-time session note is shared with gate.py and
                # bash_guard.py (hjw_common.malformed_note_once): whichever
                # hook fires first emits it, so a session that only delegates
                # still learns enforcement is off (origin 2026-09-21 F2).
                tier_pin_check = "skip:config-malformed"
                context = malformed_note_once(
                    data, payload.get("session_id", "unknown"),
                    on_codex_host(root, data))
            elif cfg["gate"]["enabled"] and cfg["gate"]["delegation_guard"]:
                # Host detection by plugin path (hjw_common.on_codex_host, the
                # same test session_brief uses to pick Codex vs Claude
                # wording); Codex spells the commands as skills (F11).
                on_codex = on_codex_host(root, data)
                override = command_name("gate off", on_codex)
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
                        f"explicit model — it runs on the host's default (Explore under a Fable host: opus) instead of a "
                        f"configured tier. {next_action} Emergency override: {override}."
                    )
                else:
                    # Tier pin check — disjoint from the generic-agent check
                    # above by construction (a tier name is never a KNOWN_
                    # GENERIC name), so the two can never fire on one call.
                    tier_pin_check, file_default, pin = _tier_pin_check(
                        subagent_type, model, requested_model, cfg, cfg_status,
                        root, on_codex)
                    if tier_pin_check == "skip:pin-not-passable":
                        context = _pin_not_passable_note_once(
                            data, payload.get("session_id", "unknown"),
                            TIER_AGENTS[subagent_type][1], pin, file_default,
                            on_codex)
                    if tier_pin_check == "deny":
                        decision = "deny"
                        # Origin 2026-08-21 silent-downgrade (kept here, out of
                        # the deny text: the user needs the next action, not
                        # the incident that earned the rule).
                        deny_reason = (
                            f"[haejwo gate] Delegation to '{subagent_type}' without a model "
                            f"override — the agent file defaults to '{file_default}' but your "
                            f"config pins '{pin}' for this tier (omission would not honor the "
                            f"pin). Pass model: '{pin}', or "
                            f"another explicit model if you intend to override the pin, or run "
                            f"{command_name('setup', on_codex)} to change it. "
                            f"Emergency override: {override}."
                        )
    except Exception:
        # Any ambiguity in the decision path fails open — still record it.
        decision = "allow"
        deny_reason = None
        context = None
        tier_pin_check = "skip:fail-open"
    context, deny_reason = with_note(ignored_note, context, deny_reason)

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
    allow(context)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open, never brick the session
