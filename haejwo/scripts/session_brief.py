#!/usr/bin/env python3
"""haejwo session-brief — SessionStart (startup|resume|clear|compact).

Injects the operating layer into every session:
- configured   -> orchestration rules + current config summary
- unconfigured -> the SAME rules + a setup nudge (repeated until configured)
  + a defaults summary
  (origin 2026-09-21 audit item 2: the defaults are enforced from the first
  turn, so the rules that explain them must ship from the first turn too —
  cold-start sessions used to get a 4-line core and nothing else)
- malformed config -> the SAME rules + a fail-open notice (NOT the setup
  nudge: nothing is enforced) + an "unreadable" summary
"""
import json
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    CODEX_SPAWN_WORKER, DEFAULT_CONFIG, PASSABLE_MODEL_ALIASES,
    gate_disabled_by_env,
    load_config_with_status, on_codex_host, paths, read_payload,
)

# Self-imposed injection budget (not a platform limit). Keep rules DISCIPLINED
# regardless — injected context costs tokens every session; the cap is a
# tripwire against silent truncation, with headroom for a few more norms.
MAX_LEN = 5000
# The config summary's own bound (cold-loop: it is appended AFTER the MAX_LEN
# check, so an unbounded summary — e.g. long model ids — defeated the cap).
# Shipped summaries run ~400-560 chars; past this it is cut at a word with
# an ellipsis rather than growing the injection without limit.
SUMMARY_MAX = 600

PLACEHOLDER = "${CLAUDE_PLUGIN_ROOT}"

# Emergency core, not a shadow ruleset: the same trusted minimum text is
# reused for two DIFFERENT causes, each with its own honest prefix, so the
# model never confuses "not configured yet" with "configuration is broken":
#  - configured session, but the FULL rules text can't be trusted this
#    session (rules file unreadable / broken install, or too large to fit
#    MAX_LEN) -> EMERGENCY_CORE (degrade prefix).
#  - session never configured at all -> UNCONFIGURED_CORE (unconfigured
#    prefix), appended after the setup nudge.
# Either way we degrade EXPLICITLY to this trusted minimum rather than
# truncating text mid-sentence.
CORE_BODY = (
    "judgment stays with the host; delegate implementation "
    "(haejwo:default-worker / haejwo:task-worker); gate limits the "
    "host's distinct code files per turn (deny = delegate); worker "
    "reports end with `Judgment calls:`; push/deploy asks first."
)
EMERGENCY_CORE = "[haejwo] emergency core (rules file unreadable or over budget): " + CORE_BODY
UNCONFIGURED_CORE = "[haejwo] not configured — minimal operating core active: " + CORE_BODY
# Codex ships no agents: its core names spawn_agent, like the nudge (2.28 F1).
_CLAUDE_WORKERS = "(haejwo:default-worker / haejwo:task-worker)"


def host_core(core, on_codex):
    """The core text for this host; the Claude text is returned unchanged."""
    return core.replace(_CLAUDE_WORKERS, "via " + CODEX_SPAWN_WORKER) if on_codex else core

# A config.json that exists but cannot be parsed is a THIRD state, and the
# unconfigured nudge is actively wrong there: it would advertise "gate ON,
# bash-guard ON" while this very status makes every hook fail open. Say what
# is true instead, and name the repair (origin 2026-09-21 review F2).
MALFORMED_NOTICE = (
    "[haejwo] config.json is unreadable (malformed JSON): enforcement is "
    "DISABLED — every gate fails open until the file is repaired. Run "
    "/haejwo:setup to rewrite it, or fix the JSON by hand."
)


def resolve_plugin_root(text, root):
    """Substitute the literal PLACEHOLDER in injected rules text with the
    CURRENTLY RUNNING hook install's resolved path (this invocation's
    root only — not a claim that it tracks the latest install).

    Only substitutes when root is usable: non-empty, absolute, and an
    existing directory. Otherwise the text is returned unchanged (the
    placeholder stays) — fail-open, never raises.
    """
    try:
        if root and os.path.isabs(root) and os.path.isdir(root):
            return text.replace(PLACEHOLDER, root.rstrip("/"))
    except Exception:
        pass
    return text


def read_rules(root):
    """The full orchestration rules text, or None when it can't be trusted."""
    try:
        with open(os.path.join(root, "rules", "orchestration.md"),
                  encoding="utf-8-sig") as f:
            return resolve_plugin_root(f.read().strip(), root)
    except Exception:
        return None


def _default_tier(v, on_codex):
    """A not-configured tier value: 'inherit' spelled as what it inherits."""
    if v == "inherit":
        return "host model" if on_codex else "session model"
    return v


def _bounded(text, limit=SUMMARY_MAX):
    """`text` cut to at most `limit` chars at a word boundary, with `…`."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _flag(on, env_off):
    """An enforcement flag as it is IN EFFECT: HAEJWO_GATE=off in the
    environment turns every hook off whatever config.json says (F12)."""
    if env_off:
        return "OFF (env)"
    return "ON" if on else "OFF"


# Codex's spawn_agent is not hooked (the delegation gate's matcher is
# Task|Agent), so on Codex the key has no effect either way (cycle 3 B4).
CODEX_DG = "n/a (spawn_agent not hooked)"


def _budget(n):
    """The budget as in effect: None is an invalid stored value, which turns
    the edit gate off (hjw_common._validate_types, cycle 3 B1)."""
    return f"{n} files/turn" if n is not None else "invalid (edit gate OFF)"


def render_summary(g, models, on_codex, configured, reviewer_on, label="",
                   env_off=False):
    """The ONE `[haejwo config]` line — gate, tiers, reviewer — for both the
    configured summary and the not-configured defaults summary (cold-loop B6:
    the two used to be rendered twice). `label` prefixes the gate fields
    (the not-configured "defaults — not configured: " wording).

    Tier values: configured -> the stored pins, verbatim on Codex ('inherit'
    = omit model, explained in the prefix), and on Claude each rendered
    honestly: "inherit" means the SESSION model for the deep-reasoner (Agent-
    tool default) but the agent file's own `model:` default for a worker, and
    a pin outside the MEASURED passable aliases (hjw_common.
    PASSABLE_MODEL_ALIASES) is flagged — delegation_gate does not enforce it
    (skip:pin-not-passable). Not configured -> the shipped defaults, with
    "inherit" spelled as the host/session model.

    Efforts: on Claude they are the agent files' `effort:` pins (2.20) — the
    only per-role effort control there; a canary test keeps these words equal
    to agents/*.md. On Codex the deep-reasoner carries NO effort of its own
    (2.14): spawn_agent omits reasoning_effort and the host's level applies.

    Gate flags describe the EFFECTIVE state (cold-loop F12/D6): `env_off`
    (HAEJWO_GATE=off) renders gate, bash_guard and delegation_guard as
    `OFF (env)`; a stored gate OFF turns both guards OFF too (they run only
    while the gate is on); delegation_guard is shown so the key is not a
    hidden switch — on Codex it reads `n/a (spawn_agent not hooked)`.
    The whole line is bounded by SUMMARY_MAX.
    """
    fork = ("effort overrides need a fresh or partial context fork "
            "(fork_turns), never a full-history fork")
    if on_codex:
        if configured:
            # Missing keys fall back to the SHIPPED defaults, never to a
            # hard-coded model name that a release bump would silently strand.
            def tier(k, worker=False):
                return models.get(k, DEFAULT_CONFIG["models_codex"][k])
            head = (f"codex tiers (pass model + reasoning_effort on spawn_agent; "
                    f"'inherit' = omit model; {fork}): ")
            tail = ""
        else:
            def tier(k, worker=False):
                return _default_tier(models[k], True)
            head = "codex tiers: "
            tail = (f" (pass reasoning_effort on spawn_agent; omit model to "
                    f"inherit; {fork})")
        tiers = (
            f"{head}deep-reasoner={tier('deep_reasoner')}"
            f"/host effort (omit reasoning_effort), "
            f"default-worker={tier('default_worker')}/medium, "
            f"task-worker={tier('task_worker')}/low{tail}"
        )
        reviewer_label = "claude reviewer"
        fallback = "disabled (fallback: native subagent, same-model)"
    else:
        if configured:
            def tier(k, worker=False):
                v = models[k]
                if v == "inherit":
                    return "agent-file default" if worker else "inherit(session)"
                if isinstance(v, str) and v.strip() not in PASSABLE_MODEL_ALIASES:
                    return f"{v} (not passable via the Agent tool — set an alias)"
                return v
        else:
            def tier(k, worker=False):
                return _default_tier(models[k], False)
        tiers = (
            f"models: deep-reasoner={tier('deep_reasoner')}, "
            f"default-worker={tier('default_worker', True)} (effort high), "
            f"task-worker={tier('task_worker', True)} (effort low)"
        )
        if configured:
            tiers += (" — pass as Agent-tool model override if it differs "
                      "from the agent default")
            if models['deep_reasoner'] == "inherit":
                tiers += " (inherit = omit the model override)"
            if "inherit" in (models['default_worker'], models['task_worker']):
                tiers += (" On Claude, omitting the model override uses each agent "
                          "file's default; pass an explicit model to override it.")
        reviewer_label = "codex reviewer"
        fallback = "disabled (fallback: deep-reasoner)"
    dg = g.get("delegation_guard", DEFAULT_CONFIG["gate"]["delegation_guard"])
    return _bounded(
        f"[haejwo config] {label}gate={_flag(g['enabled'], env_off)} "
        f"budget={_budget(g['max_files_per_turn'])} "
        f"bash_guard={_flag(g['bash_guard'] and g['enabled'], env_off)} "
        f"delegation_guard={CODEX_DG if on_codex else _flag(dg and g['enabled'], env_off)}"
        f" | {tiers} | "
        f"{reviewer_label}: {'enabled' if reviewer_on else fallback}"
    )


def main():
    os.umask(0o077)  # cold-loop F15: every hook, though this one writes nothing
    read_payload()  # consume stdin; content unused
    root, data = paths(sys.argv)
    cfg, cfg_status = load_config_with_status(data)

    # Host detection by plugin path: codex passes compat env/argv rooted under
    # /.codex/plugins (measured) — no extra probe needed. Detected BEFORE the
    # configured branch: the unconfigured nudge names the tiers too, and on
    # Codex it must never advertise Claude aliases (origin 2026-09-14: a fresh
    # Codex session was told to use Claude aliases, which it cannot pass).
    on_codex = on_codex_host(root, data)
    env_off = gate_disabled_by_env()

    if not cfg.get("configured"):
        defaults = DEFAULT_CONFIG["models_codex" if on_codex else "models"]

        # cold-loop B3: `/haejwo:gate off` (or a budget change) before setup
        # writes gate values without `configured`; report what is STORED,
        # never the defaults over it. Absent keys keep the defaults (the
        # config merge), and a malformed file reads as the defaults here.
        dg = cfg.get("gate")
        if not isinstance(dg, dict):
            dg = DEFAULT_CONFIG["gate"]
        stored = any(dg.get(k) != DEFAULT_CONFIG["gate"][k]
                     for k in ("enabled", "max_files_per_turn", "bash_guard",
                               "delegation_guard"))
        # F12: HAEJWO_GATE=off overrides whatever is stored or defaulted —
        # the nudge must not claim a gate the environment switched off.
        if stored:
            active = (
                f"Until then the STORED gate settings are ACTIVE: "
                f"gate {_flag(dg['enabled'], env_off)}, "
                + (f"max {dg['max_files_per_turn']} distinct code files per turn "
                   f"for the main agent"
                   if dg['max_files_per_turn'] is not None
                   else "edit budget invalid (edit gate OFF)")
                + f", bash-guard {_flag(dg['bash_guard'] and dg['enabled'], env_off)}, "
                f"subagents exempt. "
            )
        elif env_off:
            active = (
                "Until then the defaults apply, but HAEJWO_GATE=off in the "
                "environment overrides them: gate OFF (env), bash-guard OFF "
                "(env). "
            )
        else:
            active = (
                "Until then safe defaults are "
                "ACTIVE: gate ON, max 2 distinct code files per turn for the main agent, "
                "bash-guard ON, subagents exempt. "
            )
        if on_codex:
            active += f"Delegation guard: {CODEX_DG}. "  # cycle 3 B4
        # Codex ships no agents: name spawn_agent there (cycle 3 F4).
        targets = CODEX_SPAWN_WORKER + "." if on_codex else (
            f"haejwo:deep-reasoner ({_default_tier(defaults['deep_reasoner'], on_codex)}), "
            f"haejwo:default-worker ({_default_tier(defaults['default_worker'], on_codex)}), "
            f"haejwo:task-worker ({_default_tier(defaults['task_worker'], on_codex)})."
        )
        nudge = (
            "[haejwo] Installed but NOT configured yet (first use). Offer ONCE to "
            "configure right now, and if the user agrees RUN THE SETUP FLOW YOURSELF "
            + ("(the setup procedure — the @haejwo-setup skill; " if on_codex else
               "(the setup procedure — /haejwo:setup in Claude Code, the @haejwo-setup "
               "skill in Codex; ")
            + "the user only answers 4 quick choices and never needs "
            "to type a command). " + active + "Delegation targets: " + targets
        )
        # Defaults summary: the same fields the configured summary carries,
        # from DEFAULT_CONFIG — what is ACTUALLY enforced right now, labelled
        # as defaults (or the stored gate values) rather than as a choice.
        summary = render_summary(
            dg, defaults, on_codex, False,
            DEFAULT_CONFIG["codex"].get("enabled"),
            f"{'stored gate settings' if stored else 'defaults'} — not configured: ",
            env_off)
        malformed = cfg_status == "malformed"
        if malformed:
            # A config file that exists but cannot be parsed is NOT a fresh
            # install: never report defaults as a settled state while the
            # hooks are failing open past a file the user believes is live.
            # The setup nudge goes too — it would claim enforcement that this
            # status has switched off.
            nudge = (MALFORMED_NOTICE.replace("/haejwo:setup", "@haejwo-setup")
                     if on_codex else MALFORMED_NOTICE)  # F11
            summary = ("[haejwo config] config.json unreadable — "
                       "fail-open defaults active")
        rules = read_rules(root)
        context = "\n\n".join((rules, nudge, summary)).strip() if rules else ""
        if not rules or len(context) > MAX_LEN:
            # Same explicit degrade as the configured branch — never a
            # mid-sentence cut. Both causes are true on this path and each
            # keeps its own honest prefix. UNCONFIGURED_CORE is dropped on the
            # malformed path: "not configured" is the wrong diagnosis there,
            # and EMERGENCY_CORE already carries the same core body.
            emergency = host_core(EMERGENCY_CORE, on_codex)
            parts = ((emergency, nudge, summary) if malformed
                     else (emergency, nudge, host_core(UNCONFIGURED_CORE, on_codex),
                           summary))
            context = "\n\n".join(parts).strip()
    else:
        rules = read_rules(root)
        if rules is None:
            rules = host_core(EMERGENCY_CORE, on_codex)
        g = cfg["gate"]
        models = cfg.get("models_codex", {}) if on_codex else cfg["models"]
        summary = render_summary(g, models, on_codex, True,
                                 cfg["codex"].get("enabled"), env_off=env_off)
        context = (rules + "\n\n" + summary).strip()
        if len(context) > MAX_LEN:
            # Explicit degrade, never a mid-text cut: the full rules text
            # doesn't fit this session's budget (e.g. an oversized or
            # corrupted rules file) — swap in the trusted emergency core and
            # keep the FULL config summary, which is short and load-bearing.
            context = (host_core(EMERGENCY_CORE, on_codex) + "\n\n" + summary).strip()

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
