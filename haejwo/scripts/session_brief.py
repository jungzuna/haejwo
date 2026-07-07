#!/usr/bin/env python3
"""haejwo session-brief — SessionStart (startup|resume|clear).

Injects the operating layer into every session:
- configured   -> orchestration rules + current config summary
- unconfigured -> a one-time setup nudge (defaults still enforced meanwhile)
"""
import json
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import load_config, paths, read_payload  # noqa: E402

# Self-imposed injection budget (not a platform limit). Keep rules DISCIPLINED
# regardless — injected context costs tokens every session; the cap is a
# tripwire against silent truncation, with headroom for a few more norms.
MAX_LEN = 5000


def main():
    read_payload()  # consume stdin; content unused
    root, data = paths(sys.argv)
    cfg = load_config(data)

    if not cfg.get("configured"):
        context = (
            "[haejwo] Installed but NOT configured yet (first use). Offer ONCE to "
            "configure right now, and if the user agrees RUN THE SETUP FLOW YOURSELF "
            "(the setup procedure — /haejwo:setup in Claude Code, the @haejwo-setup "
            "skill in Codex; the user only answers 4 quick choices and never needs "
            "to type a command). Until then safe defaults are "
            "ACTIVE: gate ON, max 2 distinct code files per turn for the main agent, "
            "bash-guard ON, subagents exempt. Delegation targets: haejwo:deep-reasoner "
            "(opus), haejwo:default-worker (sonnet), haejwo:task-worker (haiku)."
        )
    else:
        try:
            with open(os.path.join(root, "rules", "orchestration.md"),
                      encoding="utf-8-sig") as f:
                rules = f.read().strip()
        except Exception:
            # Emergency core, not a shadow ruleset: a broken install
            # degrades to the load-bearing rules instead of silence.
            rules = (
                "[haejwo] rules file unreadable — emergency core: judgment "
                "stays with the host; delegate implementation "
                "(haejwo:default-worker / haejwo:task-worker); gate limits the "
                "host's distinct code files per turn (deny = delegate); worker "
                "reports end with `Judgment calls:`; push/deploy asks first."
            )
        g = cfg["gate"]
        # Host detection by plugin path: codex passes compat env/argv rooted
        # under /.codex/plugins (measured) — no extra probe needed.
        on_codex = "/.codex/" in (root or "") or "/.codex/" in (data or "")
        if on_codex:
            mc = cfg.get("models_codex", {})
            tiers = (
                f"codex tiers (pass model + reasoning_effort on spawn_agent; "
                f"'inherit' = omit model): "
                f"deep-reasoner={mc.get('deep_reasoner', 'inherit')}/high, "
                f"default-worker={mc.get('default_worker', 'gpt-5.4')}/medium, "
                f"task-worker={mc.get('task_worker', 'gpt-5.4-mini')}/low"
            )
            reviewer_label = "claude reviewer"
            fallback = "disabled (fallback: native subagent, same-model)"
        else:
            m = cfg["models"]
            tiers = (
                f"models: deep-reasoner={m['deep_reasoner']}, "
                f"default-worker={m['default_worker']}, task-worker={m['task_worker']} "
                f"(pass as Agent-tool model override if it differs from the agent default)"
            )
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

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context[:MAX_LEN],
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
