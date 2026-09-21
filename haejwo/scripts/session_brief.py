#!/usr/bin/env python3
"""haejwo session-brief — SessionStart (startup|resume|clear).

Injects the operating layer into every session:
- configured   -> orchestration rules + current config summary
- unconfigured -> the SAME rules + a one-time setup nudge + a defaults summary
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
    DEFAULT_CONFIG, load_config_with_status, paths, read_payload,
)

# Self-imposed injection budget (not a platform limit). Keep rules DISCIPLINED
# regardless — injected context costs tokens every session; the cap is a
# tripwire against silent truncation, with headroom for a few more norms.
MAX_LEN = 5000

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


def main():
    read_payload()  # consume stdin; content unused
    root, data = paths(sys.argv)
    cfg, cfg_status = load_config_with_status(data)

    # Host detection by plugin path: codex passes compat env/argv rooted under
    # /.codex/plugins (measured) — no extra probe needed. Detected BEFORE the
    # configured branch: the unconfigured nudge names the tiers too, and on
    # Codex it must never advertise Claude aliases (origin 2026-09-14: a fresh
    # Codex session was told to use sonnet/haiku, which it cannot pass).
    on_codex = "/.codex/" in (root or "") or "/.codex/" in (data or "")

    if not cfg.get("configured"):
        defaults = DEFAULT_CONFIG["models_codex" if on_codex else "models"]
        inherited = "host model" if on_codex else "session model"

        def _default_tier(v):
            return inherited if v == "inherit" else v

        targets = (
            f"haejwo:deep-reasoner ({_default_tier(defaults['deep_reasoner'])}), "
            f"haejwo:default-worker ({_default_tier(defaults['default_worker'])}), "
            f"haejwo:task-worker ({_default_tier(defaults['task_worker'])})."
        )
        nudge = (
            "[haejwo] Installed but NOT configured yet (first use). Offer ONCE to "
            "configure right now, and if the user agrees RUN THE SETUP FLOW YOURSELF "
            "(the setup procedure — /haejwo:setup in Claude Code, the @haejwo-setup "
            "skill in Codex; the user only answers 4 quick choices and never needs "
            "to type a command). Until then safe defaults are "
            "ACTIVE: gate ON, max 2 distinct code files per turn for the main agent, "
            "bash-guard ON, subagents exempt. Delegation targets: " + targets
        )
        # Defaults summary: the same gate/tiers/reviewer fields the configured
        # summary carries, computed from DEFAULT_CONFIG — what is ACTUALLY
        # enforced right now, labelled as defaults rather than as a choice.
        if on_codex:
            tiers = (
                f"codex tiers: deep-reasoner={_default_tier(defaults['deep_reasoner'])}/high, "
                f"default-worker={_default_tier(defaults['default_worker'])}/medium, "
                f"task-worker={_default_tier(defaults['task_worker'])}/low "
                f"(pass reasoning_effort on spawn_agent; omit model to inherit"
                f"; effort overrides need a fresh or partial context fork "
                f"(fork_turns), never a full-history fork)"
            )
            reviewer_label = "claude reviewer"
            fallback = "disabled (fallback: native subagent, same-model)"
        else:
            tiers = (
                f"models: deep-reasoner={_default_tier(defaults['deep_reasoner'])}, "
                f"default-worker={_default_tier(defaults['default_worker'])}, "
                f"task-worker={_default_tier(defaults['task_worker'])} (low effort)"
            )
            reviewer_label = "codex reviewer"
            fallback = "disabled (fallback: deep-reasoner)"
        dg = DEFAULT_CONFIG["gate"]
        summary = (
            f"[haejwo config] defaults — not configured: "
            f"gate={'ON' if dg['enabled'] else 'OFF'} "
            f"budget={dg['max_files_per_turn']} files/turn "
            f"bash_guard={'ON' if dg['bash_guard'] else 'OFF'} | {tiers} | "
            f"{reviewer_label}: "
            f"{'enabled' if DEFAULT_CONFIG['codex'].get('enabled') else fallback}"
        )
        malformed = cfg_status == "malformed"
        if malformed:
            # A config file that exists but cannot be parsed is NOT a fresh
            # install: never report defaults as a settled state while the
            # hooks are failing open past a file the user believes is live.
            # The setup nudge goes too — it would claim enforcement that this
            # status has switched off.
            nudge = MALFORMED_NOTICE
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
            parts = ((EMERGENCY_CORE, nudge, summary) if malformed
                     else (EMERGENCY_CORE, nudge, UNCONFIGURED_CORE, summary))
            context = "\n\n".join(parts).strip()
    else:
        rules = read_rules(root)
        if rules is None:
            rules = EMERGENCY_CORE
        g = cfg["gate"]
        if on_codex:
            mc = cfg.get("models_codex", {})
            # Missing keys fall back to the SHIPPED defaults, never to a
            # hard-coded model name that a release bump would silently strand.
            _mcx = DEFAULT_CONFIG["models_codex"]
            tiers = (
                f"codex tiers (pass model + reasoning_effort on spawn_agent; "
                f"'inherit' = omit model; effort overrides need a fresh or "
                f"partial context fork (fork_turns), never a full-history "
                f"fork): "
                f"deep-reasoner={mc.get('deep_reasoner', _mcx['deep_reasoner'])}/high, "
                f"default-worker={mc.get('default_worker', _mcx['default_worker'])}/medium, "
                f"task-worker={mc.get('task_worker', _mcx['task_worker'])}/low"
            )
            reviewer_label = "claude reviewer"
            fallback = "disabled (fallback: native subagent, same-model)"
        else:
            m = cfg["models"]

            def _tier(v, worker=False):
                # "inherit" means two DIFFERENT things on Claude: the
                # deep-reasoner inherits the SESSION model (Agent-tool
                # default), while a worker tier falls back to its own agent
                # file's `model:` default. Render each honestly.
                if v != "inherit":
                    return v
                return "agent-file default" if worker else "inherit(session)"

            tiers = (
                f"models: deep-reasoner={_tier(m['deep_reasoner'])}, "
                f"default-worker={_tier(m['default_worker'], True)}, "
                f"task-worker={_tier(m['task_worker'], True)} "
                f"(pass as Agent-tool model override if it differs from the agent default)"
            )
            if m['deep_reasoner'] == "inherit":
                tiers += " (inherit = omit the model override)"
            if "inherit" in (m['default_worker'], m['task_worker']):
                tiers += (" On Claude, omitting the model override uses each agent "
                          "file's default; pass an explicit model to override it.")
            reviewer_label = "codex reviewer"
            fallback = "disabled (fallback: deep-reasoner)"
        summary = (
            f"[haejwo config] gate={'ON' if g['enabled'] else 'OFF'} "
            f"budget={g['max_files_per_turn']} files/turn "
            f"bash_guard={'ON' if g['bash_guard'] else 'OFF'} | {tiers} | "
            f"{reviewer_label}: "
            f"{'enabled' if cfg['codex'].get('enabled') else fallback}"
        )
        context = (rules + "\n\n" + summary).strip()
        if len(context) > MAX_LEN:
            # Explicit degrade, never a mid-text cut: the full rules text
            # doesn't fit this session's budget (e.g. an oversized or
            # corrupted rules file) — swap in the trusted emergency core and
            # keep the FULL config summary, which is short and load-bearing.
            context = (EMERGENCY_CORE + "\n\n" + summary).strip()

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
